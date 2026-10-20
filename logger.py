import queue
import threading
from datetime import datetime
from messages import RuntimeStatus


class TranscriptLogger:
    def __init__(self, log_queue: queue.Queue, filepath: str, error_queue=None, status_sink=None):
        self.status_sink = status_sink
        self.log_queue   = log_queue
        self.filepath    = filepath
        self._stop_event = threading.Event()
        self._thread     = None
        self._file       = None
        self.error_queue = error_queue if error_queue is not None else queue.Queue()

    def _run(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            self._file = f
            header = f"=== Zoom 회의록 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n"
            f.write(header)
            f.flush()
            print(f"[로그] 저장 시작: {self.filepath}")
            if self.status_sink is not None:
                self.status_sink.put_nowait(RuntimeStatus("logger", "ready"))

            while not self._stop_event.is_set() or not self.log_queue.empty():
                try:
                    msg = self.log_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not msg.final:
                    continue
                now = msg.timestamp.strftime("%H:%M:%S")
                # Original and translation may be separated by other utterances.
                # Keep the same reference and recognition time on both lines.
                reference = msg.utterance_id[:8]
                if msg.kind == "en_only":
                    f.write(f"[{now}] [EN] [{reference}] {msg.text}\n\n")
                elif msg.kind == "en_ko":
                    f.write(f"[{now}] [KO] [{reference}] {msg.translation}\n\n")
                elif msg.kind == "native":
                    f.write(f"[{now}] [KO] [{reference}] {msg.text}\n\n")
                f.flush()

        print(f"[로그] 저장 완료: {self.filepath}")

    def _worker(self):
        try:
            self._run()
        except Exception as e:
            if self.status_sink is not None:
                self.status_sink.put_nowait(RuntimeStatus("logger", "error", str(e)))
            self.error_queue.put(f"회의록을 저장할 수 없습니다: {e}")

    def start(self):
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()
