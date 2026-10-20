import string
import time
from performance import record

from transcriber import (
    Transcriber, mlx_whisper, STT_TEMPERATURE, NO_SPEECH_THRESHOLD, SPEECH_RMS_FACTOR,
    MIN_BUFFER_SAMPLES,
)

POLL_INTERVAL_BY_MODEL = {
    "large-v3": 2.0, "medium": 1.5, "small": 1.0, "base": 0.8, "tiny": 0.6,
}
PHRASE_MIN_WORDS = 3


class StreamingTranscriber(Transcriber):
    """Publish cumulative stable previews; final recognition replaces the preview.

    A segment keeps one utterance ID. Each update increments its revision so
    translations of earlier hypotheses can never overwrite the final result.
    Startup, calibration and shutdown draining are shared with batch mode.
    """

    def __init__(self, model_size, audio_queue, text_queue, subtitle_queue, error_queue=None, performance=None):
        super().__init__(model_size, audio_queue, text_queue, subtitle_queue, error_queue, performance)
        self._poll_interval = POLL_INTERVAL_BY_MODEL.get(model_size, 1.5)

    def _reset_segment(self):
        super()._reset_segment()
        self._prev_words = []
        self._committed_text = ""
        self._last_poll_time = time.monotonic()

    @staticmethod
    def _norm_word(word):
        return word.lower().strip(string.punctuation)

    @staticmethod
    def _common_prefix_len(a, b):
        for i in range(min(len(a), len(b))):
            if StreamingTranscriber._norm_word(a[i]) != StreamingTranscriber._norm_word(b[i]):
                return i
        return min(len(a), len(b))

    def _poll_buffer(self, buffer, silence_rms, max_chunk_rms):
        now = time.monotonic()
        if len(buffer) < MIN_BUFFER_SAMPLES or now - self._last_poll_time < self._poll_interval:
            return
        if max_chunk_rms < silence_rms * SPEECH_RMS_FACTOR:
            return
        started = time.monotonic()
        try:
            result = mlx_whisper.transcribe(
                self._normalize(buffer), path_or_hf_repo=self._model_path,
                language=None, temperature=STT_TEMPERATURE,
                compression_ratio_threshold=2.4, logprob_threshold=-1.0,
                no_speech_threshold=NO_SPEECH_THRESHOLD, word_timestamps=False,
            )
        except Exception as e:
            print(f"[STT-스트리밍] 중간 인식 오류 (건너뜀): {e}")
            return
        finally:
            # Measure the interval from completion, including slow inference.
            self._last_poll_time = time.monotonic()
            record(self.performance, "recognition_preview", utterance_id=self._utterance_id,
                   seconds=self._last_poll_time - started, audio_seconds=len(buffer) / 16000)

        text = result.get("text", "").strip()
        if not text or self._is_hallucination(text) or result.get("language", "en") != "en":
            self._prev_words = []
            return
        words = text.split()
        stable_end = self._common_prefix_len(self._prev_words, words)
        stable_text = " ".join(words[:stable_end])
        old_words = self._committed_text.split()
        corrected = self._common_prefix_len(old_words, words[:stable_end]) < len(old_words)
        if stable_end >= PHRASE_MIN_WORDS and stable_text != self._committed_text and (
            corrected or stable_end - len(old_words) >= PHRASE_MIN_WORDS
        ):
            self._committed_text = stable_text
            self._publish_text(stable_text, final=False)
        self._prev_words = words
