"""Live transcription preview: stream words while the user is still speaking.

The recogniser re-transcribes the growing recording on a background thread and
only *commits* words two consecutive hypotheses agree on — the LocalAgreement-2
policy from UFAL's whisper_streaming (Machácek et al. 2023). Agreement filters
both word flicker (Whisper revising the tail of a hypothesis) and hallucination
on partial audio, because a hallucinated tail rarely reproduces exactly across
two different-length windows.

Committed text is monotonic — it is typed into the target app as it grows and
never retracted mid-take. When the take ends, the authoritative full-recording
transcript may still differ from what was committed (casing, punctuation, a
revised word); `reconcile` computes the minimal backspace-and-retype fix so the
final text on screen equals the final transcript exactly.

This module is engine- and UI-agnostic: it needs a PCM snapshot function, a
blocking transcribe function, and a sink with a `push(text)` method.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from threading import Event, Thread

import numpy as np

from cleanwispr.audio.gate import SILENCE_RMS_THRESHOLD
from cleanwispr.stt.base import SAMPLE_RATE

log = logging.getLogger(__name__)

_MIN_AUDIO_S = 0.6  # don't bother the engine below this
_MIN_INTERVAL_S = 0.5  # floor between preview cycles; slow engines self-pace


_WORD_EDGE_PUNCT = re.compile(r"^\W+|\W+$", re.UNICODE)


def _agreement_key(word: str) -> str:
    """Casefolded, edge-punctuation-stripped form used ONLY for the agreement
    comparison. Whisper churns capitalisation and trailing punctuation between
    hypotheses ("world." → "world,") — with exact matching, short takes commit
    almost nothing. The surface form that gets typed still comes from the
    newest hypothesis; the take-end reconcile fixes any residual drift."""
    return _WORD_EDGE_PUNCT.sub("", word).casefold()


class LocalAgreement:
    """Commit the longest common word-prefix of the two most recent hypotheses.

    Words already committed are never re-examined (monotonic): the preview may
    briefly show a word the final transcript later revises — the take-end
    reconcile pass fixes that. Words compare via _agreement_key (case- and
    punctuation-insensitive), and the newest hypothesis's LAST word is never
    committed: Whisper always closes a hypothesis with sentence-final
    punctuation that the next pass rewrites, so that word is still "hot".
    """

    def __init__(self) -> None:
        self._prev: list[str] = []
        self._committed: list[str] = []

    @property
    def committed_text(self) -> str:
        return " ".join(self._committed)

    def update(self, hypothesis: str) -> str:
        """Feed the newest full-buffer hypothesis; returns the newly committed
        words (space-joined, "" when nothing newly agreed)."""
        current = hypothesis.split()
        start = len(self._committed)
        limit = min(len(self._prev), len(current) - 1)  # hold back the hot tail
        agreed = start
        while agreed < limit and _agreement_key(self._prev[agreed]) == _agreement_key(
            current[agreed]
        ):
            agreed += 1
        fresh = current[start:agreed]  # newest surface form of the agreed words
        self._prev = current
        if fresh:
            self._committed.extend(fresh)
        return " ".join(fresh)


def reconcile(typed: str, final: str) -> tuple[int, str]:
    """Minimal edit turning `typed` (what the preview put on screen) into
    `final`: (number of backspaces, text to type after them).

    Char-level common prefix, backed off to the previous word boundary when the
    divergence is mid-word — apps with autocorrect/IME behave better when a
    whole word is retyped than when its tail is spliced.
    """
    limit = min(len(typed), len(final))
    split = 0
    while split < limit and typed[split] == final[split]:
        split += 1
    if split < len(typed) or split < len(final):
        # mid-word divergence → back off to just after the last space
        boundary = typed.rfind(" ", 0, split) + 1
        if split < limit and split > 0 and typed[split - 1] != " ":
            split = boundary
    return len(typed) - split, final[split:]


_GATE_WINDOW = SAMPLE_RATE // 10  # 100 ms, matching the speech gate's tuning


def _has_speech(pcm: np.ndarray) -> bool:
    """Cheap gate so silence-only buffers never reach the engine — Whisper
    hallucinates phrases ("Thank you.") on pure silence.

    Windowed like audio.gate (loudest 100 ms window), NOT whole-buffer RMS: a
    quiet mic or long leading silence would drag the buffer average under the
    threshold even while the user is audibly speaking, silently disabling the
    preview for the whole take."""
    usable = len(pcm) - len(pcm) % _GATE_WINDOW
    if usable < _GATE_WINDOW:
        return False
    floats = pcm[:usable].astype(np.float32) / 32768.0
    window_rms = np.sqrt(np.mean(floats.reshape(-1, _GATE_WINDOW) ** 2, axis=1))
    return float(window_rms.max()) >= SILENCE_RMS_THRESHOLD


class LiveTranscriber:
    """Background preview loop: snapshot → transcribe → agree → push.

    Serial by design: the next cycle starts only after the previous
    transcription returned, so a slow engine naturally lowers the preview rate
    instead of piling up requests (the whisper-server queues internally).
    Engine errors end the preview quietly — the take's final transcription
    will surface any real problem to the user.
    """

    def __init__(
        self,
        snapshot: Callable[[], np.ndarray],
        transcribe: Callable[[np.ndarray], str],
        sink,  # anything with push(text: str) -> None
        *,
        min_interval_s: float = _MIN_INTERVAL_S,
    ) -> None:
        self._snapshot = snapshot
        self._transcribe = transcribe
        self._sink = sink
        self._min_interval = min_interval_s
        self._agreement = LocalAgreement()
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True, name="live-preview")

    def start(self) -> None:
        self._thread.start()

    def request_stop(self) -> None:
        """Signal the loop to end without blocking (safe from the Qt thread);
        `finish` does the actual join on a worker."""
        self._stop.set()

    def finish(self, timeout_s: float = 15.0) -> None:
        """Stop the loop and wait for an in-flight transcription to return, so
        the final full-recording request never races a preview request."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout_s)

    @property
    def committed_text(self) -> str:
        return self._agreement.committed_text

    _MAX_CONSECUTIVE_FAILURES = 3

    def _run(self) -> None:
        min_samples = int(_MIN_AUDIO_S * SAMPLE_RATE)
        started = time.monotonic()
        first_commit_logged = False
        failures = 0
        while not self._stop.is_set():
            cycle_started = time.monotonic()
            pcm = self._snapshot()
            if len(pcm) >= min_samples and _has_speech(pcm):
                try:
                    hypothesis = self._transcribe(pcm)
                    failures = 0
                except Exception as exc:
                    # a single hiccup (server mid-restart, timeout) must not
                    # cost the whole take its preview — retry a few times
                    failures += 1
                    log.info(
                        "live preview transcribe failed (%d/%d): %s",
                        failures, self._MAX_CONSECUTIVE_FAILURES, exc,
                    )
                    if failures >= self._MAX_CONSECUTIVE_FAILURES:
                        log.warning("live preview stopped after repeated engine errors")
                        return
                    self._stop.wait(0.5)
                    continue
                if self._stop.is_set():
                    return  # take already ended; leave the rest to reconcile
                fresh = self._agreement.update(hypothesis)
                if fresh:
                    if not first_commit_logged:
                        first_commit_logged = True
                        log.info(
                            "live preview: first words committed %.1fs into the take",
                            time.monotonic() - started,
                        )
                    self._sink.push(fresh)
            elapsed = time.monotonic() - cycle_started
            self._stop.wait(max(0.05, self._min_interval - elapsed))
