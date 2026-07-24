"""Live transcription preview: LocalAgreement commits, reconcile deltas,
LiveTranscriber loop, and the LiveTypingSink safety rules."""

from __future__ import annotations

import time

import numpy as np

from cleanwispr.inject.live import LiveResult, LiveTypingSink
from cleanwispr.stt.live import LiveTranscriber, LocalAgreement, reconcile

# --- LocalAgreement ---


def test_agreement_commits_only_agreed_prefix():
    agreement = LocalAgreement()
    assert agreement.update("hello") == ""  # first hypothesis: nothing to agree with
    assert agreement.update("hello world") == "hello"  # both start with "hello"
    assert agreement.committed_text == "hello"


def test_agreement_holds_back_unstable_tail():
    agreement = LocalAgreement()
    agreement.update("hello wor")
    fresh = agreement.update("hello world")  # "wor" != "world" → only "hello" commits
    assert fresh == "hello"
    fresh = agreement.update("hello world how")
    assert fresh == "world"  # now "world" agreed across the last two
    assert agreement.committed_text == "hello world"


def test_agreement_is_monotonic_when_hypothesis_shrinks():
    agreement = LocalAgreement()
    agreement.update("one two three")
    agreement.update("one two three")
    # "three" stays hot (last word of the hypothesis) — reconcile emits it later
    assert agreement.committed_text == "one two"
    assert agreement.update("one") == ""  # a shrinking hypothesis never retracts
    assert agreement.committed_text == "one two"


def test_agreement_ignores_punctuation_and_case_churn():
    # Whisper closes every hypothesis with punctuation and re-punctuates the
    # tail on the next pass; agreement must see through that churn
    agreement = LocalAgreement()
    agreement.update("hello world.")
    fresh = agreement.update("Hello world, this is")
    assert fresh == "Hello world,"  # newest surface form wins
    fresh = agreement.update("Hello world, this is great")
    assert fresh == "this is"
    assert agreement.committed_text == "Hello world, this is"


def test_agreement_never_commits_the_hot_last_word():
    agreement = LocalAgreement()
    agreement.update("done")
    assert agreement.update("done") == ""  # sole word = last word = still hot
    assert agreement.committed_text == ""


def test_agreement_filters_flickering_hallucination():
    agreement = LocalAgreement()
    agreement.update("Thank you.")
    # hallucinations rarely repeat identically across different-length windows
    assert agreement.update("So today we") == ""
    assert agreement.update("So today we are") == "So today we"


# --- reconcile ---


def test_reconcile_identical_is_noop():
    assert reconcile("hello world", "hello world") == (0, "")


def test_reconcile_appends_suffix():
    assert reconcile("hello", "hello world") == (0, " world")


def test_reconcile_extends_mid_word():
    assert reconcile("hel", "hello") == (0, "lo")


def test_reconcile_mid_word_divergence_backs_off_to_word_boundary():
    backspaces, addition = reconcile("hello wold", "hello world")
    assert backspaces == 4  # erase "wold", not just "ld"
    assert addition == "world"


def test_reconcile_punctuation_and_case_fix():
    backspaces, addition = reconcile("hello world", "Hello, world!")
    assert ("hello world"[: 11 - backspaces] + addition) == "Hello, world!"


def test_reconcile_shorter_final_erases_rest():
    assert reconcile("one two three", "one two") == (6, "")


# --- LiveTranscriber ---


class CollectingSink:
    def __init__(self):
        self.pushes = []

    def push(self, text):
        self.pushes.append(text)


def test_live_transcriber_pushes_committed_words():
    speech = (np.random.default_rng(1).integers(-3000, 3000, 32000)).astype(np.int16)
    hypotheses = iter(["hello", "hello world", "hello world again", "hello world again"])

    sink = CollectingSink()
    live = LiveTranscriber(
        snapshot=lambda: speech,
        transcribe=lambda pcm: next(hypotheses, "hello world again"),
        sink=sink,
        min_interval_s=0.01,
    )
    live.start()
    deadline = time.monotonic() + 5
    # "again" stays hot (last word) — the final reconcile delivers it
    while " ".join(sink.pushes) != "hello world" and time.monotonic() < deadline:
        time.sleep(0.02)
    live.finish()
    assert " ".join(sink.pushes) == "hello world"


def test_live_transcriber_skips_silence():
    calls = []
    silence = np.zeros(32000, dtype=np.int16)
    live = LiveTranscriber(
        snapshot=lambda: silence,
        transcribe=lambda pcm: calls.append(1) or "Thank you.",
        sink=CollectingSink(),
        min_interval_s=0.01,
    )
    live.start()
    time.sleep(0.15)
    live.finish()
    assert calls == []  # silence never reaches the engine (hallucination guard)


def test_live_transcriber_survives_transient_engine_error():
    speech = np.full(32000, 2000, dtype=np.int16)
    hypotheses = iter(["boom", "hello world", "hello world again"])

    def flaky(pcm):
        value = next(hypotheses, "hello world again")
        if value == "boom":
            raise RuntimeError("server mid-restart")
        return value

    sink = CollectingSink()
    live = LiveTranscriber(lambda: speech, flaky, sink, min_interval_s=0.01)
    live.start()
    deadline = time.monotonic() + 5
    while " ".join(sink.pushes) != "hello world" and time.monotonic() < deadline:
        time.sleep(0.02)
    live.finish()
    assert " ".join(sink.pushes) == "hello world"  # one hiccup didn't end the preview


def test_live_transcriber_gives_up_after_repeated_errors():
    speech = np.full(32000, 2000, dtype=np.int16)
    calls = []

    def broken(pcm):
        calls.append(1)
        raise RuntimeError("server died")

    sink = CollectingSink()
    live = LiveTranscriber(lambda: speech, broken, sink, min_interval_s=0.01)
    live.start()
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    live.finish(timeout_s=5)
    assert len(calls) == 3  # three strikes, then the loop ends for good
    assert sink.pushes == []


def test_speech_gate_hears_quiet_speech_after_leading_silence():
    from cleanwispr.stt.live import _has_speech

    # 1.9 s of silence + one quiet 100 ms burst: the whole-buffer average is
    # far below the threshold, but the windowed gate must still pass it
    quiet_take = np.zeros(32000, dtype=np.int16)
    quiet_take[30400:32000] = 150  # window RMS ≈ 0.0046 > 0.002
    assert _has_speech(quiet_take) is True
    assert _has_speech(np.zeros(32000, dtype=np.int16)) is False


# --- LiveTypingSink ---


class FakeTypingInjector:
    supports_live_typing = True

    def __init__(self):
        self.screen = ""
        self.copied = None
        self.modifiers = False
        self.focus = 111

    def modifiers_held(self):
        return self.modifiers

    def focus_token(self):
        return self.focus

    def type_text(self, text):
        self.screen += text

    def delete_chars(self, count):
        self.screen = self.screen[: len(self.screen) - count]

    def copy_text(self, text):
        self.copied = text


def test_sink_types_words_with_spacing():
    injector = FakeTypingInjector()
    sink = LiveTypingSink(injector)
    sink.push("hello")
    sink.push("world again")
    assert injector.screen == "hello world again"


def test_sink_finalize_corrects_in_place():
    injector = FakeTypingInjector()
    sink = LiveTypingSink(injector)
    sink.push("hello wold")
    assert sink.finalize("Hello, world!") is LiveResult.DONE
    assert injector.screen == "Hello, world!"


def test_sink_untouched_when_nothing_typed():
    injector = FakeTypingInjector()
    sink = LiveTypingSink(injector)
    assert sink.finalize("hello") is LiveResult.UNTOUCHED
    assert injector.screen == ""


def test_sink_buffers_while_modifiers_held():
    injector = FakeTypingInjector()
    injector.modifiers = True
    sink = LiveTypingSink(injector)
    sink.push("hello")
    assert injector.screen == ""  # held → nothing typed yet
    injector.modifiers = False
    sink.push("world")
    assert injector.screen == "hello world"  # flushed together


def test_sink_freezes_on_focus_change():
    injector = FakeTypingInjector()
    sink = LiveTypingSink(injector)
    sink.push("hello")
    injector.focus = 222  # user alt-tabbed
    sink.push("world")
    assert injector.screen == "hello"  # nothing typed into the new window
    assert sink.finalize("hello world") is LiveResult.FROZEN
    assert injector.screen == "hello"


def test_sink_rollback_erases_preview():
    injector = FakeTypingInjector()
    sink = LiveTypingSink(injector)
    sink.push("hello world")
    sink.rollback()
    assert injector.screen == ""


def test_sink_never_types_newlines():
    injector = FakeTypingInjector()
    sink = LiveTypingSink(injector)
    sink.push("hello\nworld")
    assert "\n" not in injector.screen
