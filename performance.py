"""Timing-only sidecar diagnostics; never store spoken text or translations."""
import json
import logging
import threading
import time


def record(sink, stage, **values):
    if sink is not None:
        sink(stage, **values)
    else:
        logging.getLogger("zoomtrans.performance").info("%s %s", stage, values)


class PerformanceLogger:
    def __init__(self, filepath):
        self.filepath = filepath
        self._lock = threading.Lock()
        self._file = None
        self._closed = False

    def __call__(self, stage, **values):
        with self._lock:
            if self._closed:
                return
            try:
                if self._file is None:
                    self._file = open(self.filepath, "a", encoding="utf-8")
                self._file.write(json.dumps({"time": time.time(), "stage": stage, **values}) + "\n")
                self._file.flush()
            except OSError as exc:
                logging.getLogger(__name__).warning("진단 기록 실패: %s", exc)
                self._close()

    def _close(self):
        self._closed = True
        if self._file:
            try:
                self._file.close()
            except OSError as exc:
                logging.getLogger(__name__).warning("진단 기록 종료 실패: %s", exc)
            self._file = None

    def stop(self):
        with self._lock:
            self._close()
