"""Worker lifecycle without GUI dependencies."""

import threading


def stop_pipeline(workers, on_progress=lambda name: None):
    errors = []
    for name, worker in workers:
        on_progress(name)
        try:
            worker.stop()
        except Exception as exc:
            errors.append(f"{name} 종료 실패: {exc}")
    return errors


class MeetingSession:
    def __init__(self, capture, transcriber, translator, logger, subtitle_queue,
                 error_queue, log_filename, model, performance=None):
        self.capture = capture
        self.transcriber = transcriber
        self.translator = translator
        self.logger = logger
        self.subtitle_queue = subtitle_queue
        self.error_queue = error_queue
        self.log_filename = log_filename
        self.model = model
        self.performance = performance
        self._closing = threading.Event()
        self._startup_thread = None

    def start(self):
        def worker():
            try:
                for component in (self.logger, self.transcriber, self.translator, self.capture):
                    if self._closing.is_set():
                        break
                    component.start()
            except Exception as exc:
                self.error_queue.put(f"프로그램을 시작할 수 없습니다: {exc}")

        self._startup_thread = threading.Thread(target=worker, daemon=True)
        self._startup_thread.start()

    def stop(self, on_progress=lambda name: None):
        self._closing.set()
        self.translator.begin_drain()
        if self._startup_thread:
            self._startup_thread.join()
        workers = [
            ("오디오 캡처", self.capture), ("음성인식", self.transcriber),
            ("번역", self.translator), ("회의록 저장", self.logger),
        ]
        if self.performance:
            workers.append(("진단 기록", self.performance))
        return stop_pipeline(workers, on_progress)
