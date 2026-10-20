"""Messages shared by STT, translation, subtitles and the transcript writer."""

import threading
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SubtitleEvent:
    kind: str
    utterance_id: str = ""
    revision: int = 0
    text: str = ""
    translation: str = ""
    final: bool = True
    error: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    language: str = "en"
    incomplete: bool = False
    finish_reason: str = "sentence"
    queued_at: float = 0.0
    speech_ended_at: float = 0.0
    stt_seconds: float = 0.0
    audio_seconds: float = 0.0
    translation_model: str = ""
    translation_fallback: bool = False


@dataclass(frozen=True)
class AudioChunk:
    """Capture timestamp preserved through calibration and STT backlog."""

    samples: object
    captured_at: float = 0.0


@dataclass(frozen=True)
class RuntimeStatus:
    """Operational state only; never written to the meeting transcript."""

    component: str
    state: str
    detail: str = ""
    level: float = 0.0


class Broadcaster:
    """Publish originals before translation jobs; reject superseded results.

    The log queue must be unbounded so a slow disk never silently drops speech.
    Only final recognition/translation results belong in the transcript.
    """

    LOGGABLE_TYPES = {"en_only", "en_ko", "native"}

    def __init__(self, subtitle_queue, log_queue):
        self._subtitle_queue = subtitle_queue
        self._log_queue = log_queue
        self._latest = {}
        self._lock = threading.RLock()

    def is_current(self, event):
        with self._lock:
            current = self._latest.get(event.utterance_id)
            return current is None or (
                current.revision == event.revision and current.kind == "en_only"
            )

    def put_nowait(self, event):
        with self._lock:
            if isinstance(event, RuntimeStatus):
                self._subtitle_queue.put_nowait(event)
                return
            if event.kind in {"en_only", "native", "discard"}:
                current = self._latest.get(event.utterance_id)
                if current and event.revision <= current.revision:
                    return
                self._latest[event.utterance_id] = event
            elif event.utterance_id and not self.is_current(event):
                return
            self._subtitle_queue.put_nowait(event)
            if event.final and event.kind in self.LOGGABLE_TYPES:
                self._log_queue.put_nowait(event)
