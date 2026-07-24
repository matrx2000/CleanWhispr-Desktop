"""Central application state machine and dictation pipeline.

idle → recording → transcribing → injecting → idle

The controller is the only component that talks to every subsystem
(audio, stt, inject, storage). UI components observe it via Qt signals and
never import engines directly. Heavy work (transcription, injection) runs on
a single worker thread; results come back to the Qt main thread through
queued signal connections.
"""

from __future__ import annotations

import logging
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from threading import Event, Thread

from PySide6.QtCore import QObject, QTimer, Signal

from cleanwispr.audio.capture import AudioError, Recorder
from cleanwispr.inject.base import InjectError, TextInjector
from cleanwispr.inject.live import LiveResult, LiveTypingSink
from cleanwispr.llm import factory as llm_factory
from cleanwispr.llm import server as ollama_server
from cleanwispr.llm.base import LlmProviderError
from cleanwispr.llm.prompts import (
    build_edit_messages,
    build_generate_messages,
    build_whole_note_messages,
    clean_llm_output,
)
from cleanwispr.llm.toolloop import ToolConfirmRequest, run_tool_loop
from cleanwispr.storage import paths
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import ActivationMode, Settings
from cleanwispr.stt.base import SAMPLE_RATE, SttError
from cleanwispr.stt.live import LiveTranscriber
from cleanwispr.stt.whisper_cpp import WhisperCppEngine
from skillkit import voice
from skillkit.library import SkillLibrary
from skillkit.store import MemorySkillStore

log = logging.getLogger(__name__)


class AppState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    EDITING = "editing"  # editor mode: LLM is applying the instruction (M3)
    INJECTING = "injecting"
    ERROR = "error"


class SessionKind(StrEnum):
    DICTATION = "dictation"
    EDIT = "edit"
    NOTES_DICTATION = "notes_dictation"  # dictate into the Notes editor (no injection)
    NOTES_AI = "notes_ai"  # AI take inside Notes: LLM edits the note (no injection)


# AI-take context modes for the Notes view (mirrors the Flutter EditorPromptMode)
NOTES_MODE_SELECTION = "selection"  # edit the highlighted range
NOTES_MODE_WHOLE = "whole_note"  # operate on the entire note
NOTES_MODE_GENERATE = "generate"  # empty note — create from the instruction


@dataclass(slots=True)
class _PipelineOutcome:
    ok: bool
    kind: SessionKind = SessionKind.DICTATION
    text: str = ""
    message: str = ""  # user-facing notice on failure/skip
    language: str | None = None
    engine: str | None = None
    llm_model: str | None = None
    instruction: str | None = None
    source_text: str | None = None
    duration_ms: int = 0
    audio_path: str | None = None
    switch_only: bool = False  # a voice skill-switch: no edit ran, nothing to paste/log


class Controller(QObject):
    state_changed = Signal(AppState)
    error_occurred = Signal(str)
    notice = Signal(str)  # transient user-facing info ("No speech detected")
    edit_status = Signal(str)  # editor-session narration for the overlay (sticky)
    edit_thinking = Signal(str)  # streamed reasoning deltas for the thinking panel
    recording_starting = Signal()  # fires BEFORE the mic opens (audio cues must beat
    # the Bluetooth headset profile switch that recording triggers)
    mic_ready = Signal()  # first audio frames arrived — the mic is really live
    level_changed = Signal(float)  # mic RMS while recording, for level UIs
    history_changed = Signal()
    notes_text_ready = Signal(str)  # transcribed dictation → the Notes editor
    notes_ai_ready = Signal(object)  # (result, mode) AI take → the Notes editor
    # a ToolConfirmRequest the UI must answer (queued to the main thread); the
    # worker blocks on request.wait() and treats a timeout as "denied"
    tool_confirm = Signal(object)

    _outcome_ready = Signal(object)  # _PipelineOutcome, worker → main thread
    _stage_changed = Signal(AppState)  # mid-pipeline state updates from the worker

    def __init__(
        self,
        settings: Settings,
        db: HistoryDb,
        recorder: Recorder,
        engine: WhisperCppEngine | dict,
        injector: TextInjector,
        skills: SkillLibrary | None = None,
        tools=None,  # toolkit.ToolLibrary | None — optional, like skills
    ) -> None:
        super().__init__()
        self.settings = settings
        self._db = db
        # the skills layer is optional and self-contained; a disabled in-memory
        # library keeps the feature a pure no-op when the host doesn't wire one
        self._skills = skills or SkillLibrary(MemorySkillStore())
        self._tools = tools
        self._tools_notice_shown: set[str] = set()  # once-per-model "no tools" notice
        self._recorder = recorder
        # engine: a single SttEngine (used for every stt.engine setting) or a
        # dict of {"whisper": ..., "parakeet": ...}
        self._engines = engine if isinstance(engine, dict) else {"whisper": engine}
        self._injector = injector
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")
        self._state = AppState.IDLE
        self._session_kind: SessionKind | None = None
        self._press_started: float | None = None
        # pending source/mode for a Notes AI take (set before recording starts)
        self._notes_ai_source: str = ""
        self._notes_ai_mode: str = NOTES_MODE_GENERATE
        self._notes_ai_images: list[str] = []  # base64 images from the selection (vision)
        # live-typing preview for the current dictation take (M-live)
        self._live: LiveTranscriber | None = None
        self._live_sink: LiveTypingSink | None = None
        self._live_take: tuple[LiveTranscriber | None, LiveTypingSink | None] = (None, None)
        self._outcome_ready.connect(self._on_outcome)
        self._stage_changed.connect(self._set_state)
        # abort takes where the mic never delivers audio (dead/slow BT endpoints)
        self._mic_watchdog = QTimer(self)
        self._mic_watchdog.setSingleShot(True)
        self._mic_watchdog.setInterval(4000)
        self._mic_watchdog.timeout.connect(self._on_mic_timeout)
        self.mic_ready.connect(self._mic_watchdog.stop)

    @property
    def state(self) -> AppState:
        return self._state

    def _set_state(self, state: AppState) -> None:
        if state is not self._state:
            self._state = state
            self.state_changed.emit(state)

    # --- hotkey entry points (press/release from the hotkey backend) ---

    _TAP_LATCH_S = 0.3  # hold-mode releases shorter than this latch the recording on

    def hotkey_pressed(self, slot: str) -> None:
        """Key-down for a slot. Starts recording, or stops it (toggle semantics;
        in hold mode a second press only happens after a tap-latched start)."""
        if self._state is AppState.IDLE:
            self._press_started = time.monotonic()
            if slot == "editor":
                self.toggle_editor()
            else:
                self._start_recording(SessionKind.DICTATION)
        elif self._state is AppState.RECORDING:
            self._finish_recording()

    def hotkey_released(self, slot: str) -> None:
        """Key-up for a slot — only meaningful in push-to-hold mode."""
        slot_settings = getattr(self.settings.hotkeys, slot, None)
        if slot_settings is None or slot_settings.mode is not ActivationMode.HOLD:
            return
        if self._state is not AppState.RECORDING:
            return
        held_for = time.monotonic() - (self._press_started or 0)
        if held_for >= self._TAP_LATCH_S:
            self._finish_recording()
        # shorter than the latch: treat as a tap — keep recording, next press stops

    # --- entry points (tray, overlay clicks — always toggle semantics) ---

    def toggle_dictation(self) -> None:
        if self._state is AppState.IDLE:
            self._start_recording(SessionKind.DICTATION)
        elif self._state is AppState.RECORDING:
            self._finish_recording()

    def toggle_editor(self) -> None:
        if self._state is AppState.IDLE:
            self._start_recording(SessionKind.EDIT)
        elif self._state is AppState.RECORDING:
            self._finish_recording()

    # --- Notes view entry points (driven by the in-window slider) ---

    def toggle_notes_dictation(self) -> None:
        """Slider left: dictate into the Notes editor (result via notes_text_ready)."""
        if self._state is AppState.IDLE:
            self._start_recording(SessionKind.NOTES_DICTATION)
        elif self._state is AppState.RECORDING:
            self._finish_recording()

    def start_notes_ai(
        self, source_text: str, mode: str, images: list[str] | None = None
    ) -> None:
        """Slider right: record an instruction, then have the LLM apply it to the
        note. `mode` is one of NOTES_MODE_SELECTION / _WHOLE / _GENERATE; the
        result comes back via notes_ai_ready as (result, mode). `images` are
        base64-encoded pictures in the selection, sent only to vision models."""
        if self._state is AppState.IDLE:
            self._notes_ai_source = source_text or ""
            self._notes_ai_mode = mode
            self._notes_ai_images = images or []
            self._start_recording(SessionKind.NOTES_AI)
        elif self._state is AppState.RECORDING:
            self._finish_recording()

    def notes_finish(self) -> None:
        """Stop an active Notes take (slider tap while recording)."""
        if self._state is AppState.RECORDING:
            self._finish_recording()

    def cancel(self) -> None:
        if self._state is AppState.RECORDING:
            self._mic_watchdog.stop()
            self._recorder.abort()
            self._discard_live_preview()
            self._session_kind = None
            self._set_state(AppState.IDLE)
            self.notice.emit("Recording cancelled")

    def _active_engine(self, settings: Settings):
        """(engine, model_id) for the configured STT engine, whisper fallback."""
        stt = settings.stt
        if stt.engine == "parakeet" and "parakeet" in self._engines:
            return self._engines["parakeet"], stt.parakeet_model
        return self._engines["whisper"], stt.whisper_model

    def prewarm(self) -> None:
        """Load the configured STT engine in the background so dictation is fast."""

        def warm() -> None:
            try:
                stt = self.settings.stt
                engine, model_id = self._active_engine(self.settings)
                engine.ensure(model_id, stt.language, stt.gpu)
            except SttError as exc:
                log.info("prewarm skipped: %s", exc)

        self._executor.submit(warm)

    def shutdown(self) -> None:
        self._recorder.abort()
        if self._live is not None:
            self._live.request_stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        for engine in self._engines.values():
            engine.stop()

    # --- pipeline ---

    def _start_recording(self, kind: SessionKind) -> None:
        self.recording_starting.emit()
        try:
            self._recorder.start(
                device_name=self.settings.audio.input_device,
                on_level=self.level_changed.emit,
                on_first_frame=self.mic_ready.emit,
            )
        except AudioError as exc:
            self._fail(str(exc))
            return
        self._session_kind = kind
        if kind is SessionKind.DICTATION:
            self._maybe_start_live_preview()
        self._mic_watchdog.start()
        self._set_state(AppState.RECORDING)

    # --- live-typing preview (dictation only) ---

    def _maybe_start_live_preview(self) -> None:
        """Start the streaming preview when the setting is on, the injector can
        type, and the target is not a terminal (where synthetic backspaces go
        through the shell's line editor instead of editing text)."""
        if not self.settings.stt.live_typing:
            return
        injector = self._injector
        if not getattr(injector, "supports_live_typing", False):
            return
        try:
            if injector.focus_is_terminal():
                log.info("live typing skipped: focused window is a terminal")
                return
        except Exception:
            log.exception("terminal detection failed; skipping live typing")
            return
        settings = self.settings.model_copy(deep=True)
        stt = settings.stt
        engine, model_id = self._active_engine(settings)
        prompt = ", ".join(stt.custom_dictionary) or None

        def transcribe(pcm) -> str:
            engine.ensure(model_id, stt.language, stt.gpu)
            return engine.transcribe(pcm, language=stt.language, initial_prompt=prompt).text

        sink = LiveTypingSink(injector)
        self._live_sink = sink
        self._live = LiveTranscriber(self._recorder.snapshot, transcribe, sink)
        self._live.start()
        log.info("live preview started (%s:%s)", stt.engine, model_id)

    def _take_live_preview(self) -> None:
        """Move the running preview into the pending take (consumed by
        _run_dictation on the worker) and stop producing new commits."""
        live, sink = self._live, self._live_sink
        self._live = self._live_sink = None
        if live is not None:
            live.request_stop()
        self._live_take = (live, sink)

    def _discard_live_preview(self) -> None:
        """Abort the preview and erase anything it typed (cancelled/empty take).
        The join and the backspaces run on the worker — never the Qt thread."""
        live, sink = self._live, self._live_sink
        self._live = self._live_sink = None
        if live is None:
            return
        live.request_stop()

        def cleanup() -> None:
            live.finish()
            if sink is not None:
                sink.rollback()

        self._executor.submit(cleanup)

    def _on_mic_timeout(self) -> None:
        if self._state is AppState.RECORDING:
            self._recorder.abort()
            self._discard_live_preview()
            self._session_kind = None
            self._set_state(AppState.IDLE)
            self.notice.emit(
                "Microphone produced no audio — Bluetooth mics can take a moment; "
                "try again or pick another device in Settings → Audio"
            )

    _MIN_AUDIO_SAMPLES = SAMPLE_RATE // 3  # ~0.33s — below this the engine has nothing to work with

    def _finish_recording(self) -> None:
        self._mic_watchdog.stop()
        pcm, decision = self._recorder.stop()
        if decision.skip:
            self._discard_live_preview()
            self._session_kind = None
            self._set_state(AppState.IDLE)
            self.notice.emit("No speech detected")
            return
        if len(pcm) < self._MIN_AUDIO_SAMPLES:
            # e.g. a Bluetooth mic still connecting when the take ended
            self._discard_live_preview()
            self._session_kind = None
            self._set_state(AppState.IDLE)
            self.notice.emit("Almost no audio captured — was the microphone ready?")
            return
        self._take_live_preview()
        self._set_state(AppState.TRANSCRIBING)
        settings_snapshot = self.settings.model_copy(deep=True)
        kind = self._session_kind or SessionKind.DICTATION
        job = {
            SessionKind.EDIT: self._run_edit,
            SessionKind.NOTES_DICTATION: self._run_notes,
            SessionKind.NOTES_AI: self._run_notes_ai,
        }.get(kind, self._run_dictation)
        self._executor.submit(self._guarded, job, pcm, settings_snapshot)

    def _guarded(self, job, pcm, settings: Settings) -> None:
        """Worker-thread wrapper. Must NEVER raise — an escaped exception would
        leave the UI stuck."""
        try:
            job(pcm, settings)
        except Exception as exc:
            log.exception("pipeline crashed")
            self._outcome_ready.emit(
                _PipelineOutcome(ok=False, message=f"Internal error: {exc}")
            )

    def _transcribe(self, pcm, settings: Settings):
        """Shared STT leg; returns the result or None after emitting a failure."""
        stt = settings.stt
        try:
            engine, model_id = self._active_engine(settings)
            engine.ensure(model_id, stt.language, stt.gpu)
            prompt = ", ".join(stt.custom_dictionary) or None
            return engine.transcribe(pcm, language=stt.language, initial_prompt=prompt)
        except SttError as exc:
            self._outcome_ready.emit(_PipelineOutcome(ok=False, message=str(exc)))
            return None

    def _inject_into_outcome(self, text: str, settings: Settings, outcome) -> None:
        try:
            self._injector.inject(text, restore_clipboard=settings.inject.restore_clipboard)
        except InjectError as exc:
            outcome.message = f"{exc}"  # text stays on clipboard; still record history
        except Exception as exc:
            log.exception("paste failed")
            outcome.message = f"Paste failed ({exc}) — text is on the clipboard"

    def _attach_notes_images(self, messages, provider, model: str) -> None:
        """Attach the selection's images to the user message — but only if the
        model advertises vision. Otherwise skip them and say why."""
        if not self._notes_ai_images:
            return
        try:
            has_vision = provider.supports_vision(model)
        except Exception:
            log.exception("vision capability check failed")
            has_vision = False
        count = len(self._notes_ai_images)
        if not has_vision:
            log.info("skipping %d image(s): %s reports no vision support", count, model)
            self.notice.emit(
                f"{count} image(s) skipped — {model} can't read images "
                "(try gemma3:4b or llava)"
            )
            return
        log.info("attaching %d image(s) to %s", count, model)
        for message in messages:
            if message.role == "user":
                message.images = list(self._notes_ai_images)
                break

    def _apply_skill_overrides(self, options, scope: str) -> None:
        """Let the active skills pin temperature/model (last active wins). No-op
        when the feature is off or no active skill overrides them."""
        temperature = self._skills.resolved_temperature(scope)
        if temperature is not None:
            options.temperature = temperature
        model = self._skills.resolved_model(scope)
        if model:
            options.model = model

    def _run_dictation(self, pcm, settings: Settings) -> None:
        live, sink = self._live_take
        self._live_take = (None, None)
        if live is not None:
            # wait out any in-flight preview request so the final transcription
            # never races it on the engine
            live.finish()
        result = self._transcribe(pcm, settings)
        if result is None:
            return  # engine error; leave any preview text in place — it is real speech
        # if the final pass returns nothing but stable words were already typed,
        # the committed preview is the best transcript we have — keep it
        final_text = result.text or (sink.typed if sink is not None else "")
        if not final_text:
            self._outcome_ready.emit(_PipelineOutcome(ok=False, message="Nothing transcribed"))
            return

        audio_path = None
        if settings.audio.keep_recordings:
            audio_path = self._save_recording(pcm)

        outcome = _PipelineOutcome(
            ok=True,
            text=final_text,
            language=result.language,
            engine=f"{settings.stt.engine}:{self._active_engine(settings)[1]}",
            duration_ms=result.duration_ms,
            audio_path=audio_path,
        )
        self._settle_dictation_screen(sink, final_text, settings, outcome)
        self._outcome_ready.emit(outcome)

    def _settle_dictation_screen(
        self, sink: LiveTypingSink | None, final_text: str, settings: Settings, outcome
    ) -> None:
        """Get the final text on screen: correct the live preview in place when
        there is one, paste classically otherwise, clipboard fallback when the
        preview lost its window."""
        if sink is None:
            self._inject_into_outcome(final_text, settings, outcome)
            return
        live_result = sink.finalize(final_text)
        if live_result is LiveResult.DONE:
            return  # screen already shows exactly final_text
        if live_result is LiveResult.UNTOUCHED:
            self._inject_into_outcome(final_text, settings, outcome)
            return
        # FROZEN: focus moved mid-preview — never type into the new window
        try:
            self._injector.copy_text(final_text)
            outcome.message = (
                "Focus changed during live typing — the full text is on the clipboard"
            )
        except Exception:
            log.exception("clipboard fallback failed")
            outcome.message = "Focus changed during live typing and the clipboard copy failed"

    def _run_edit(self, pcm, settings: Settings) -> None:
        """Editor session: the transcript is an instruction. Capture the selected
        text, have the local LLM apply the instruction, paste the result over
        the selection (or at the cursor when nothing is selected)."""
        self.edit_status.emit("Transcribing your command…")
        result = self._transcribe(pcm, settings)
        if result is None:
            return
        instruction = result.text
        if not instruction:
            self._outcome_ready.emit(_PipelineOutcome(ok=False, message="No instruction heard"))
            return
        self.edit_status.emit(f"“{instruction}”")

        # a spoken skill switch ("switch to poet", "plain") arms/clears skills
        # instead of running an edit — voice switching is editor-only (M-skills)
        if self._skills.enabled and self._skills.config.voice_switching:
            verdict = voice.parse_switch(instruction, self._skills)
            if verdict.outcome != voice.PASSTHROUGH:
                if verdict.outcome == voice.APPLIED:
                    self._skills.apply_verdict(verdict)
                self._outcome_ready.emit(
                    _PipelineOutcome(
                        ok=True,
                        kind=SessionKind.EDIT,
                        switch_only=True,
                        message=verdict.notice,
                    )
                )
                return

        try:
            selection = self._injector.capture_selection()
        except Exception:
            log.exception("selection capture failed")
            selection = None

        self._stage_changed.emit(AppState.EDITING)
        self.edit_thinking.emit(self._session_preamble(instruction, selection))
        options = llm_factory.chat_options(settings.llm)
        active_skills = self._skills.active_skills(scope="editor")
        self._apply_skill_overrides(options, "editor")
        try:
            provider = llm_factory.create_provider(settings.llm)
            if not provider.is_available():
                self.edit_status.emit("Ollama isn't running — trying to start it…")
                if not ollama_server.ensure_running(provider):
                    self._outcome_ready.emit(
                        _PipelineOutcome(
                            ok=False,
                            kind=SessionKind.EDIT,
                            message="Ollama is not running and could not be started — "
                            "install it from ollama.com or start it manually.",
                        )
                    )
                    return
            if selection:
                messages = build_edit_messages(instruction, selection, active_skills)
            else:
                messages = build_generate_messages(instruction, active_skills)
            edited = clean_llm_output(self._chat_leg(provider, messages, options))
        except LlmProviderError as exc:
            self._outcome_ready.emit(
                _PipelineOutcome(ok=False, kind=SessionKind.EDIT, message=str(exc))
            )
            return

        if not edited:
            self._outcome_ready.emit(
                _PipelineOutcome(
                    ok=False, kind=SessionKind.EDIT, message="The model returned nothing"
                )
            )
            return

        outcome = _PipelineOutcome(
            ok=True,
            kind=SessionKind.EDIT,
            text=edited,
            language=result.language,
            engine=f"{settings.stt.engine}:{self._active_engine(settings)[1]}",
            llm_model=f"{settings.llm.provider}:{options.model}",
            instruction=instruction,
            source_text=selection,
            duration_ms=result.duration_ms,
        )
        self.edit_status.emit("Pasting result…")
        self._inject_into_outcome(edited, settings, outcome)
        self._outcome_ready.emit(outcome)

    def _run_notes(self, pcm, settings: Settings) -> None:
        """Notes dictation: transcribe and hand the text to the Notes editor via
        a signal — no injection into a foreign app."""
        result = self._transcribe(pcm, settings)
        if result is None:
            return
        if not result.text:
            self._outcome_ready.emit(_PipelineOutcome(ok=False, message="Nothing transcribed"))
            return
        self.notes_text_ready.emit(result.text)
        # log to history like any dictation take
        self._outcome_ready.emit(
            _PipelineOutcome(
                ok=True,
                kind=SessionKind.DICTATION,
                text=result.text,
                language=result.language,
                engine=f"{settings.stt.engine}:{self._active_engine(settings)[1]}",
                duration_ms=result.duration_ms,
            )
        )

    def _run_notes_ai(self, pcm, settings: Settings) -> None:
        """Notes AI take: the transcript is an instruction applied by the LLM to
        the note's source text (selection / whole note / nothing → generate). The
        result returns to the editor via notes_ai_ready — no injection."""
        source = self._notes_ai_source
        mode = self._notes_ai_mode
        self.edit_status.emit("Transcribing your command…")
        result = self._transcribe(pcm, settings)
        if result is None:
            return
        instruction = result.text
        if not instruction:
            self._outcome_ready.emit(_PipelineOutcome(ok=False, message="No instruction heard"))
            return
        self.edit_status.emit(f"“{instruction}”")

        self._stage_changed.emit(AppState.EDITING)
        self.edit_thinking.emit(self._session_preamble(instruction, source or None))
        options = llm_factory.chat_options(settings.llm)
        active_skills = self._skills.active_skills(scope="notes")
        self._apply_skill_overrides(options, "notes")
        try:
            provider = llm_factory.create_provider(settings.llm)
            if not provider.is_available():
                self.edit_status.emit("Ollama isn't running — trying to start it…")
                if not ollama_server.ensure_running(provider):
                    self._outcome_ready.emit(
                        _PipelineOutcome(
                            ok=False,
                            kind=SessionKind.EDIT,
                            message="Ollama is not running and could not be started — "
                            "install it from ollama.com or start it manually.",
                        )
                    )
                    return
            if mode == NOTES_MODE_WHOLE and source:
                messages = build_whole_note_messages(instruction, source, active_skills)
            elif mode == NOTES_MODE_SELECTION and source:
                messages = build_edit_messages(instruction, source, active_skills)
            else:
                messages = build_generate_messages(instruction, active_skills)
            self._attach_notes_images(messages, provider, options.model)
            edited = clean_llm_output(self._chat_leg(provider, messages, options))
        except LlmProviderError as exc:
            self._outcome_ready.emit(
                _PipelineOutcome(ok=False, kind=SessionKind.EDIT, message=str(exc))
            )
            return

        if not edited:
            self._outcome_ready.emit(
                _PipelineOutcome(
                    ok=False, kind=SessionKind.EDIT, message="The model returned nothing"
                )
            )
            return

        self.notes_ai_ready.emit((edited, mode))
        self._outcome_ready.emit(
            _PipelineOutcome(
                ok=True,
                kind=SessionKind.EDIT,
                text=edited,
                language=result.language,
                engine=f"{settings.stt.engine}:{self._active_engine(settings)[1]}",
                llm_model=f"{settings.llm.provider}:{options.model}",
                instruction=instruction,
                source_text=source or None,
                duration_ms=result.duration_ms,
            )
        )

    @staticmethod
    def _session_preamble(instruction: str, selection: str | None) -> str:
        """Markdown header for the thinking panel: what was sent to the model."""
        parts = [f"**Command:** {instruction}\n\n"]
        if selection:
            shown = selection if len(selection) <= 600 else selection[:600] + " …"
            quoted = "> " + shown.replace("\n", "\n> ")
            parts.append(f"**Selected text** ({len(selection)} chars):\n\n{quoted}\n\n")
        else:
            parts.append("*No text selected — generating from the command alone.*\n\n")
        parts.append("---\n\n")
        return "".join(parts)

    def _chat_leg(self, provider, messages, options) -> str:
        """The LLM leg shared by editor and Notes-AI sessions: narrate model
        load, then stream — through the tool loop when tools are armed and the
        model supports function calling, plain streaming otherwise."""
        library = self._tools
        if library is not None and library.config.enabled and library.armed_specs():
            try:
                tools_ok = provider.supports_tools(options.model)
            except Exception:
                log.exception("tool capability check failed")
                tools_ok = False
            if tools_ok:
                self._prepare_model(provider, options)
                on_thinking, on_content = self._narration_callbacks(options)
                return run_tool_loop(
                    provider,
                    messages,
                    options,
                    library,
                    on_status=self.edit_status.emit,
                    on_thinking=on_thinking,
                    on_content=on_content,
                    request_confirm=self._request_tool_confirm,
                )
            if options.model not in self._tools_notice_shown:
                self._tools_notice_shown.add(options.model)
                self.notice.emit(
                    f"{options.model} doesn't support tools — running without them "
                    "(qwen3 and llama3.1 do)"
                )
        return self._stream_with_status(provider, messages, options)

    def _request_tool_confirm(self, spec, args: dict) -> bool:
        """Worker-side confirmation: hand the question to the UI thread and
        block until answered (timeout ⇒ denied, so headless runs stay safe)."""
        request = ToolConfirmRequest(tool=spec, args=args)
        self.tool_confirm.emit(request)
        return request.wait(timeout_s=120)

    def _prepare_model(self, provider, options) -> None:
        """Narrated model warm-up: an explicit load phase with a live seconds
        counter when the model is cold."""
        loaded = provider.is_model_loaded(options.model)
        if loaded is False:
            self._load_model_with_ticker(provider, options)
            self.edit_status.emit(f"{options.model} loaded — thinking…")
        elif loaded:
            self.edit_status.emit(f"{options.model} ready — thinking…")
        else:
            self.edit_status.emit(f"Waiting for {options.model}…")

    def _narration_callbacks(self, options):
        """(on_thinking, on_content) that keep the overlay pill narrated."""
        state = {"thinking": False, "chunks": 0, "chars": 0}

        def on_thinking(text: str) -> None:
            # full reasoning streams to the thinking panel; the pill stays compact
            if not state["thinking"]:
                state["thinking"] = True
                self.edit_status.emit(f"💭 {options.model} thinking…")
            self.edit_thinking.emit(text)

        def on_content(chunk: str) -> None:
            state["chunks"] += 1
            state["chars"] += len(chunk)
            if state["chunks"] == 1 or state["chunks"] % 20 == 0:
                self.edit_status.emit(f"{options.model}: writing… {state['chars']} chars")

        return on_thinking, on_content

    def _stream_with_status(self, provider, messages, options) -> str:
        """Consume the plain (tool-less) LLM stream with narration."""
        self._prepare_model(provider, options)
        on_thinking, on_content = self._narration_callbacks(options)
        chunks: list[str] = []
        for chunk in provider.chat(messages, options, on_thinking=on_thinking):
            chunks.append(chunk)
            on_content(chunk)
        return "".join(chunks)

    def _load_model_with_ticker(self, provider, options) -> None:
        """Block until Ollama has the model in memory, ticking the overlay."""
        self.edit_status.emit(f"Loading {options.model} into memory…")
        done = Event()
        started = time.monotonic()

        def tick() -> None:
            while not done.wait(1.0):
                elapsed = int(time.monotonic() - started)
                self.edit_status.emit(f"Loading {options.model} into memory… {elapsed}s")

        ticker = Thread(target=tick, daemon=True, name="ollama-load-ticker")
        ticker.start()
        try:
            provider.load_model(options.model, options.keep_alive)
        finally:
            done.set()

    def _on_outcome(self, outcome: _PipelineOutcome) -> None:
        """Main thread: persist history, settle state."""
        if outcome.switch_only:
            # a voice skill-switch — announce it, nothing to paste or record
            if outcome.message:
                self.notice.emit(outcome.message)
            self._set_state(AppState.IDLE)
            self._session_kind = None
            return
        if outcome.ok:
            self._set_state(AppState.INJECTING)
            if self.settings.history.enabled:
                self._db.add(
                    outcome.kind.value,
                    outcome.text,
                    instruction=outcome.instruction,
                    source_text=outcome.source_text,
                    language=outcome.language,
                    engine=outcome.engine,
                    llm_model=outcome.llm_model,
                    duration_ms=outcome.duration_ms,
                    audio_path=outcome.audio_path,
                )
                self.history_changed.emit()
            if outcome.message:
                self.notice.emit(outcome.message)
            self._set_state(AppState.IDLE)
        else:
            self._fail(outcome.message)
        self._session_kind = None

    def _fail(self, message: str) -> None:
        log.error("pipeline failed: %s", message)
        self._set_state(AppState.ERROR)
        self.error_occurred.emit(message)
        self._set_state(AppState.IDLE)

    @staticmethod
    def _save_recording(pcm) -> str:
        path = paths.recordings_dir() / f"rec-{time.strftime('%Y%m%d-%H%M%S')}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm.tobytes())
        return str(path)
