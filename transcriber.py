import queue
import re
import math
import threading
import time
from collections import deque
from uuid import uuid4

from messages import AudioChunk, SubtitleEvent, RuntimeStatus
from sentence_boundary import is_incomplete, split_sentences
from performance import record
import numpy as np

class _WhisperBackend:
    @staticmethod
    def transcribe(*args, **kwargs):
        # Importing MLX initializes Metal. Keep it on the worker thread so
        # device listing and opening the application never load the model.
        import mlx_whisper
        return mlx_whisper.transcribe(*args, **kwargs)


mlx_whisper = _WhisperBackend()

SAMPLE_RATE = 16000

MLX_MODELS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium":   "mlx-community/whisper-medium-mlx",
    "small":    "mlx-community/whisper-small-mlx",
    "base":     "mlx-community/whisper-base-mlx",
    "tiny":     "mlx-community/whisper-tiny-mlx",
}

# 침묵 감지 파라미터
SILENCE_TRIGGER   = 0.6
FORCE_SILENCE = 1.2
SENTENCE_SCAN = 8.0
SCAN_INTERVAL = 4.0
# Rotate the recognition window, never finalize an unfinished sentence by time.
MAX_BUFFER        = 28.0
CHUNK_SAMPLES = int(SAMPLE_RATE * 0.1)
MIN_BUFFER        = 0.5  # "Yes" 같은 짧은 단답도 인식 대상에 포함 (기존 1.0)
CALIBRATION_SEC   = 2.0

SILENCE_TRIGGER_SAMPLES = int(SAMPLE_RATE * SILENCE_TRIGGER)
MAX_BUFFER_SAMPLES      = int(SAMPLE_RATE * MAX_BUFFER)
MIN_BUFFER_SAMPLES      = int(SAMPLE_RATE * MIN_BUFFER)

NO_SPEECH_THRESHOLD = 0.6
AUDIO_TARGET_RMS    = 0.1
SPEECH_RMS_FACTOR   = 1.1

# 디코드 실패(압축률/logprob 임계 미달) 시 whisper가 더 높은 temperature로 재시도하도록
# fallback 튜플 사용 → 환각/깨진 결과 감소. 정상 구간은 0.0(greedy)로 끝나 속도 영향 없음.
STT_TEMPERATURE = (0.0, 0.2, 0.4)
# 짧은/저신호 버퍼에 직전 문장을 initial_prompt로 주면 whisper가 그 문장을 반복하는
# 환각이 잘 생긴다. 버퍼가 이 길이 이상일 때만 프롬프트를 준다("Yes/Okay" 단답 보호).
PROMPT_MIN_SAMPLES = int(SAMPLE_RATE * 1.5)


class Transcriber:
    def __init__(self, model_size: str, audio_queue: queue.Queue,
                 text_queue: queue.Queue, subtitle_queue, error_queue=None, performance=None):
        self.performance = performance
        self.audio_queue    = audio_queue
        self.text_queue     = text_queue
        self.subtitle_queue = subtitle_queue
        self._model_path    = MLX_MODELS.get(model_size, MLX_MODELS["large-v3"])
        self._stop_event    = threading.Event()
        self._thread        = None
        self._prev_text     = ""
        self._prev_lang     = "en"
        self.error_queue = error_queue if error_queue is not None else queue.Queue()
        self._calibration_chunks = []
        self._reset_segment()

    def _warmup(self):
        """모델을 로드하고 첫 추론을 준비한다."""
        print(f"[STT] 모델 로딩 중: {self._model_path}")
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        mlx_whisper.transcribe(silence, path_or_hf_repo=self._model_path)
        print("[STT] 모델 로딩 완료")

    def _calibrate_silence_threshold(self) -> float:
        print(f"[STT] 노이즈 바닥 측정 중... ({CALIBRATION_SEC:.0f}초)")
        rms_values = []
        deadline = time.monotonic() + CALIBRATION_SEC
        while time.monotonic() < deadline and not self._stop_event.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.3)
                self._calibration_chunks.append(chunk)
                samples = chunk.samples if isinstance(chunk, AudioChunk) else chunk
                rms_values.append(float(np.sqrt(np.mean(samples ** 2))))
            except queue.Empty:
                continue

        if not rms_values:
            return 0.005

        noise_floor = float(np.percentile(rms_values, 20))

        # BlackHole 같은 가상 장치는 노이즈가 없어 캘리브레이션 중 소리가 있으면
        # 실제 배경 노이즈가 아닌 신호를 측정한 것 → 고정 낮은 임계값 사용
        if noise_floor > 0.008:
            print(f"[STT] 캘리브레이션 중 오디오 감지 → 낮은 고정 임계값 사용")
            threshold = 0.005
        else:
            threshold = float(np.clip(noise_floor * 3, 0.003, 0.05))

        print(f"[STT] 노이즈 바닥: {noise_floor:.6f} → 침묵 임계값: {threshold:.6f}")
        return threshold

    def _normalize(self, audio: np.ndarray) -> np.ndarray:
        rms = np.sqrt(np.mean(audio ** 2))
        if rms < 1e-9:
            return audio
        return audio / rms * AUDIO_TARGET_RMS

    def _is_hallucination(self, text: str) -> bool:
        cleaned = text.lower().replace(" ", "").replace(".", "").replace(",", "")
        if len(cleaned) < 4:
            return False
        if len(set(cleaned)) <= 3 and len(cleaned) > 6:
            return True
        words = text.lower().split()
        if len(words) >= 4 and len(set(words)) <= 2:
            return True
        return False

    def _reset_segment(self):
        self._utterance_id = uuid4().hex
        self._revision = 0
        self._candidate = None
        self._force_boundary = ""
        self._checked = False
        self._speech_ended_at = 0.0
        self._stt_seconds = 0.0
        self._audio_seconds = 0.0
        self._carry_text = ""
        self._scan_mode = False
        self._retain_from = None
        self._next_scan = int(SAMPLE_RATE * SENTENCE_SCAN)

    def _poll_buffer(self, buffer, silence_rms, max_chunk_rms):
        """Streaming mode overrides this hook."""

    def _run(self):
        self.subtitle_queue.put_nowait(RuntimeStatus("stt", "loading"))
        self._warmup()
        self.subtitle_queue.put_nowait(RuntimeStatus("stt", "calibrating"))
        silence_rms = self._calibrate_silence_threshold()
        self.subtitle_queue.put_nowait(RuntimeStatus("stt", "ready"))
        pending = deque(self._calibration_chunks)
        self._calibration_chunks.clear()
        buffer = np.array([], dtype=np.float32)
        silence_samples = 0
        max_chunk_rms = 0.0

        def finalize(reason=""):
            nonlocal buffer, silence_samples, max_chunk_rms
            self._scan_mode = reason == "sentence_scan"
            self._retain_from = None
            self._force_boundary = "" if self._scan_mode else reason
            self._audio_seconds = len(buffer) / SAMPLE_RATE
            complete = True
            if len(buffer) >= MIN_BUFFER_SAMPLES or (reason == "shutdown" and len(buffer)):
                complete = self._transcribe(buffer, silence_rms, max_chunk_rms)
            if self._retain_from is not None:
                buffer = buffer[self._retain_from:]
                silence_samples = min(silence_samples, len(buffer))
                self._candidate = None
                self._checked = False
                self._next_scan = max(int(SAMPLE_RATE * SENTENCE_SCAN),
                                      len(buffer) + int(SAMPLE_RATE * SCAN_INTERVAL))
                return
            if complete is False:
                if self._scan_mode:
                    self._next_scan = len(buffer) + int(SAMPLE_RATE * SCAN_INTERVAL)
                else:
                    self._checked = True
                return
            buffer = np.array([], dtype=np.float32)
            silence_samples = 0
            max_chunk_rms = 0.0
            self._reset_segment()
            self.subtitle_queue.put_nowait(RuntimeStatus("stt", "ready"))

        while True:
            try:
                if pending:
                    chunk = pending.popleft()
                elif self._stop_event.is_set():
                    chunk = self.audio_queue.get_nowait()
                else:
                    chunk = self.audio_queue.get(timeout=0.3)
            except queue.Empty:
                if self._stop_event.is_set():
                    finalize("shutdown")
                    break
                # Missing capture data is not acoustic silence.
                continue

            captured_at = chunk.captured_at if isinstance(chunk, AudioChunk) else 0.0
            chunk = chunk.samples if isinstance(chunk, AudioChunk) else chunk
            if not len(chunk):
                continue
            # Handle oversized inputs/calibration chunks without exceeding the
            # same boundaries used for normal 100ms capture callbacks.
            if len(chunk) > CHUNK_SAMPLES:
                pieces = [AudioChunk(chunk[i:i + CHUNK_SAMPLES],
                          captured_at - (len(chunk) - min(i + CHUNK_SAMPLES, len(chunk))) / SAMPLE_RATE
                          if captured_at else 0.0)
                          for i in range(0, len(chunk), CHUNK_SAMPLES)]
                pending.extendleft(reversed(pieces))
                continue
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            voiced = rms >= silence_rms
            if not len(buffer) and not voiced:
                continue
            buffer = np.concatenate([buffer, chunk])
            max_chunk_rms = max(max_chunk_rms, rms)
            silence_samples = silence_samples + len(chunk) if not voiced else 0
            if voiced:
                self._speech_ended_at = captured_at
                self._candidate = None
                self._checked = False
                self.subtitle_queue.put_nowait(RuntimeStatus("stt", "collecting"))
            if silence_samples >= int(SAMPLE_RATE * FORCE_SILENCE):
                finalize("silence_limit")
            elif silence_samples >= SILENCE_TRIGGER_SAMPLES and not self._checked:
                finalize()
            elif len(buffer) >= self._next_scan:
                finalize("sentence_scan")
            elif not self._stop_event.is_set() and not self._checked:
                self._poll_buffer(buffer, silence_rms, max_chunk_rms)

    def _publish_text(self, text, language="en", final=True, incomplete=False, finish_reason=""):
        self._revision += 1
        event = SubtitleEvent(
            "en_only" if language == "en" else "native",
            utterance_id=self._utterance_id,
            revision=self._revision,
            text=text,
            final=final, language=language, incomplete=incomplete, finish_reason=finish_reason,
            queued_at=time.monotonic() if final else 0.0,
            speech_ended_at=self._speech_ended_at, stt_seconds=self._stt_seconds,
            audio_seconds=self._audio_seconds,
        )
        # Publish the original first, so even a very fast translator cannot
        # overtake it. The ID/revision also handles out-of-order consumers.
        self.subtitle_queue.put_nowait(event)
        if final:
            self.text_queue.put(event)

    def _discard_preview(self):
        if self._revision:
            self._revision += 1
            self.subtitle_queue.put_nowait(SubtitleEvent(
                "discard", utterance_id=self._utterance_id, revision=self._revision,
            ))

    def _transcribe(self, audio: np.ndarray, silence_rms: float, max_chunk_rms: float):
        if max_chunk_rms < silence_rms * SPEECH_RMS_FACTOR:
            self._discard_preview()
            return

        if self._candidate is not None and not self._scan_mode:
            return self._finish_candidate()

        audio = self._normalize(audio)
        use_prompt     = self._prev_text and self._prev_lang == "en" and len(audio) >= PROMPT_MIN_SAMPLES
        initial_prompt = self._prev_text[-200:] if use_prompt else None

        started = time.monotonic()
        try:
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=self._model_path,
                language=None,          # 자동 감지 (한국어/영어 분기 유지)
                initial_prompt=initial_prompt,
                temperature=STT_TEMPERATURE,
                compression_ratio_threshold=2.4,
                logprob_threshold=-1.0,
                no_speech_threshold=NO_SPEECH_THRESHOLD,
                word_timestamps=self._scan_mode,
            )
        except Exception as e:
            print(f"[STT] 인식 오류 (이 구간 건너뜀): {e}")
            if not self._scan_mode:
                self._discard_preview()
            self.subtitle_queue.put_nowait(SubtitleEvent("status", text=f"음성인식 실패: {e}"))
            return False if self._scan_mode else None
        finally:
            duration = time.monotonic() - started
            self._stt_seconds += duration
            record(self.performance, "recognition", utterance_id=self._utterance_id,
                   seconds=duration, audio_seconds=len(audio) / SAMPLE_RATE)

        detected_lang = result.get("language", "en")
        full_text     = result.get("text", "").strip()

        if not full_text or self._is_hallucination(full_text):
            if full_text:
                print(f"[STT] 환각 폐기: {full_text[:40]}")
            if not self._scan_mode:
                self._discard_preview()
            return False if self._scan_mode else None

        if self._scan_mode:
            return self._finish_scan(full_text, detected_lang, result, len(audio))
        self._candidate = (self._join_carry(full_text), detected_lang)
        return self._finish_candidate()

    def _join_carry(self, text):
        return f"{self._carry_text} {text}".strip() if self._carry_text else text

    @staticmethod
    def _compact(text):
        return re.sub(r"\s+", "", text).casefold()

    def _finish_scan(self, text, language, result, audio_samples):
        """Commit only completed prefixes, keeping the trailing sentence/audio.

        Word timestamps locate the actual audio cut. Without trustworthy alignment
        retain everything and retry, rather than losing or repeating words.
        """
        words = [word for segment in result.get("segments", [])
                 for word in segment.get("words", []) if word.get("word", "").strip()]
        if self._compact("".join(word["word"] for word in words)) != self._compact(text):
            words = []
        try:
            if not all(math.isfinite(float(word["start"])) and math.isfinite(float(word["end"]))
                       and 0 <= float(word["start"]) <= float(word["end"]) <= audio_samples / SAMPLE_RATE
                       for word in words):
                words = []
        except (KeyError, TypeError, ValueError):
            words = []
        eligible = []
        for part in split_sentences(text)[:-1]:
            if is_incomplete(part, language):
                break
            eligible.append(part)
        count = 0
        if eligible:
            target = self._compact(" ".join(eligible))
            assembled = ""
            for index, word in enumerate(words):
                assembled += self._compact(word["word"])
                if assembled == target:
                    count = index + 1
                    break
                if not target.startswith(assembled):
                    break
        rotate = not count and audio_samples >= MAX_BUFFER_SAMPLES
        if rotate:
            # Carry the unfinished text; keep at least two seconds of original
            # audio, and cut only between words. Nothing is translated yet.
            for index, word in enumerate(words[:-1]):
                if float(word.get("end", float("inf"))) <= audio_samples / SAMPLE_RATE - 2:
                    count = index + 1
        if 0 < count < len(words):
            end = float(words[count - 1].get("end", -1))
            start = float(words[count].get("start", -1))
            cut = int((end + start) / 2 * SAMPLE_RATE)
            if 0 <= end <= start and 0 < cut < audio_samples:
                if rotate:
                    prefix = " ".join(word["word"].strip() for word in words[:count])
                    self._carry_text = self._join_carry(prefix).rstrip(".!?。！？")
                else:
                    self._publish_parts(self._join_carry(" ".join(eligible)), language,
                                        finish_reason="sentence")
                    self._prev_text, self._prev_lang = self._join_carry(" ".join(eligible)), language
                    self._carry_text = ""
                    # The tail is a new utterance; translations of the prefix
                    # must remain current while the next sentence is collected.
                    self._utterance_id, self._revision = uuid4().hex, 0
                self._retain_from = cut
                return False
        if language == "en":
            self._publish_text(self._join_carry(text), language, final=False)
        return False

    def _publish_parts(self, text, language, incomplete=False, finish_reason="sentence"):
        parts, pending = [], ""
        for part in split_sentences(text) or [text]:
            part = f"{pending.rstrip('.!?。！？')}, {part}" if pending else part
            if is_incomplete(part, language):
                pending = part
            else:
                parts.append(part)
                pending = ""
        if pending:
            parts.append(pending)
        for index, part in enumerate(parts):
            if index:
                self._utterance_id, self._revision = uuid4().hex, 0
            self._publish_text(part, language,
                               incomplete=incomplete and index == len(parts) - 1,
                               finish_reason=finish_reason)

    def _finish_candidate(self):
        text, language = self._candidate
        incomplete = is_incomplete(text, language)
        if incomplete and not self._force_boundary:
            if language == "en":
                self._publish_text(text, language, final=False)
            self.subtitle_queue.put_nowait(RuntimeStatus("stt", "waiting", "다음 구절을 잠시 기다리는 중"))
            return False
        self._publish_parts(text, language,
                            incomplete=incomplete or self._force_boundary == "shutdown",
                            finish_reason=self._force_boundary or "sentence")
        self._prev_text, self._prev_lang = text, language
        return True

    def _worker(self):
        try:
            self._run()
        except Exception as e:
            message = f"음성인식을 시작하거나 계속할 수 없습니다: {e}"
            print(f"[STT] {message}")
            self.subtitle_queue.put_nowait(RuntimeStatus("stt", "error", message))
            self.error_queue.put(message)

    def start(self):
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        print("[STT] 중지")
