"""A single Qt event loop for setup, live subtitles, shutdown and summary."""

import queue
import signal
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer, Signal, Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QProgressBar, QTextBrowser, QVBoxLayout,
)

from overlay import SubtitleOverlay
from summarizer import MeetingSummarizer
from ui_widgets import apply_application_theme, button, label


class MeetingApplication(QApplication):
    quit_requested = Signal()

    def event(self, event):
        # Native application/Dock quit requests must drain workers too.
        if event.type() == QEvent.Type.Quit:
            self.quit_requested.emit()
            return True
        return super().event(event)


class ChoiceDialog(QDialog):
    selected = Signal(object)

    def __init__(self, title, message, options, default, parent=None):
        super().__init__(parent)
        self.default = default
        self._chosen = False
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 22)
        layout.setSpacing(18)
        heading = label(message)
        heading.setFont(QFont("Apple SD Gothic Neo", 16, QFont.Weight.DemiBold))
        layout.addWidget(heading)
        row = QHBoxLayout()
        for i, (text, value) in enumerate(options):
            control = button(text, primary=i == 0)
            control.clicked.connect(lambda checked=False, value=value: self.choose(value))
            row.addWidget(control)
        layout.addLayout(row)

    def choose(self, value):
        if not self._chosen:
            self._chosen = True
            self.hide()
            self.selected.emit(value)
            self.accept()

    def reject(self):
        self.choose(self.default)


class ProgressDialog(QDialog):
    def __init__(self, title, message, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self.message = label(message)
        layout.addWidget(self.message)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        layout.addWidget(self.bar)

    def reject(self):
        # Closing a progress window must not abandon workers or saved output.
        pass


class SummaryDialog(ProgressDialog):
    completed = Signal(str, str)

    def __init__(self, log_filename, model):
        super().__init__("회의록 요약", "회의 내용을 정리하고 있습니다…")
        self.resize(640, 460)
        self.browser = QTextBrowser()
        self.browser.setFont(QFont("Apple SD Gothic Neo", 13))
        self.browser.setOpenExternalLinks(False)
        self.layout().addWidget(self.browser, 1)
        self.close_button = button("닫기", self.accept, primary=True)
        self.close_button.setEnabled(False)
        self.layout().addWidget(self.close_button)
        self.completed.connect(self._complete)
        self._finished = False
        self._log_filename = log_filename
        self._model = model

    def start(self):
        def worker():
            try:
                text = MeetingSummarizer(self._model).summarize_file(self._log_filename)
                path = Path(self._log_filename).with_name(Path(self._log_filename).stem + "_summary.txt")
                try:
                    path.write_text(text, encoding="utf-8")
                except OSError as exc:
                    self.completed.emit(text, f"요약 파일 저장 실패: {exc}")
                else:
                    self.completed.emit(text, f"저장됨 · {path}")
            except Exception as exc:
                self.completed.emit("", f"요약 실패: {exc}\n회의록 원본은 저장되어 있습니다: {self._log_filename}")

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _complete(self, text, status):
        self._finished = True
        self.bar.hide()
        self.message.setText(status)
        if text:
            self.browser.setMarkdown(text)
        self.close_button.setEnabled(True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.show()

    def reject(self):
        if self._finished:
            QDialog.reject(self)


class DesktopController(QObject):
    stopped = Signal(object)
    progress = Signal(str)

    def __init__(self, app, args, session_factory, has_transcript):
        super().__init__()
        self.app = app
        self.args = args
        self.session_factory = session_factory
        self.has_transcript = has_transcript
        self.session = None
        self.overlay = None
        self.dialog = None
        self.errors = []
        self.exit_code = 0
        self.closing = False
        self._old_signals = {}
        self.error_timer = QTimer(self)
        self.error_timer.setInterval(100)
        self.error_timer.timeout.connect(self._poll_errors)
        self.stopped.connect(self._stopped)
        self.progress.connect(self._progress)
        self.app.setQuitOnLastWindowClosed(False)
        apply_application_theme(app)
        self.app.paletteChanged.connect(self._theme_changed)
        if isinstance(self.app, MeetingApplication):
            self.app.quit_requested.connect(self.shutdown)
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._old_signals[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda *args: self.shutdown())

    def _theme_changed(self, *args):
        if getattr(self, "_changing_theme", False):
            return
        self._changing_theme = True
        try:
            apply_application_theme(self.app)
        finally:
            self._changing_theme = False

    def start(self):
        from setup_dialog import SetupDialog
        self.setup_dialog = SetupDialog(self.args)
        self.setup_dialog.ready.connect(self._choose_mode)
        self.setup_dialog.cancelled.connect(self.finish)
        self.setup_dialog.show()
        self.setup_dialog.check()

    def _choose_mode(self):
        if self.args.device.lower() == "system" and not self.args.silent:
            self.dialog = ChoiceDialog(
                "ZoomTrans", "회의 소리를 어떻게 들을까요?",
                [("스피커로 듣기", False), ("무음 · 자막만", True)], False,
            )
            self.dialog.selected.connect(self._start_session)
            self.dialog.show()
        else:
            self._start_session(self.args.silent)

    def _start_session(self, silent):
        if self.closing:
            return
        try:
            self.session = self.session_factory(self.args, silent)
            self.overlay = SubtitleOverlay(self.session.subtitle_queue,
                                           performance=getattr(self.session, "performance", None))
            self.overlay.set_complex_translation(self.session.translator.complex_enabled,
                                                 self.session.translator.complex_model)
            self.overlay.complex_changed.connect(self.session.translator.set_complex_enabled)
            self.overlay.shutdown_requested.connect(self.shutdown)
            for screen in self.app.screens():
                screen.availableGeometryChanged.connect(self.overlay.ensure_on_screen)
            self.app.screenRemoved.connect(self.overlay.ensure_on_screen)
            self.overlay.show()
            self.error_timer.start()
            self.session.start()
        except Exception as exc:
            self.errors.append(f"프로그램을 시작할 수 없습니다: {exc}")
            self.shutdown()

    def _poll_errors(self):
        if not self.session or self.closing:
            return
        try:
            while True:
                self.errors.append(self.session.error_queue.get_nowait())
        except queue.Empty:
            pass
        if self.errors:
            self.shutdown()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.error_timer.stop()
        if self.dialog:
            self.dialog.hide()
        if self.overlay:
            self.overlay.request_shutdown()
            self.overlay.stop()
        if self.session is None:
            self._stopped([])
            return
        # Switch to draining before waiting for any startup or STT work.
        self.session.translator.begin_drain()
        self.dialog = ProgressDialog("회의 마무리", "남은 발화와 회의록을 저장하고 있습니다…")
        self.dialog.show()

        def worker():
            try:
                errors = self.session.stop(self.progress.emit)
            except Exception as exc:
                errors = [f"프로그램 종료 실패: {exc}"]
            self.stopped.emit(errors)

        self._shutdown_thread = threading.Thread(target=worker, daemon=True)
        self._shutdown_thread.start()

    def _progress(self, name):
        if isinstance(self.dialog, ProgressDialog):
            self.dialog.message.setText(f"{name} 마무리 중…\n남은 발화와 회의록을 저장하고 있습니다.")

    def _stopped(self, errors):
        self.errors.extend(errors)
        if self.dialog:
            self.dialog.hide()
        if self.session:
            try:
                while True:
                    self.errors.append(self.session.error_queue.get_nowait())
            except queue.Empty:
                pass
        if self.errors:
            self.exit_code = 1
            dialog = QDialog()
            dialog.setWindowTitle("ZoomTrans · 오류")
            dialog.resize(520, 320)
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.addWidget(label("작업 중 문제가 발생했습니다."))
            details = QTextBrowser()
            details.setPlainText("\n\n".join(dict.fromkeys(self.errors)))
            layout.addWidget(details, 1)
            layout.addWidget(button("닫기", dialog.accept, primary=True))
            dialog.finished.connect(self.finish)
            self.dialog = dialog
            dialog.show()
        elif self.session and self.has_transcript(self.session.log_filename):
            self.dialog = ChoiceDialog(
                "회의록 저장 완료", "회의록을 요약할까요?",
                [("요약 만들기", True), ("마치기", False)], False,
            )
            self.dialog.selected.connect(self._summary_choice)
            self.dialog.show()
        else:
            self.finish()

    def _summary_choice(self, generate):
        if generate:
            self.dialog = SummaryDialog(self.session.log_filename, self.session.model)
            self.dialog.finished.connect(self.finish)
            self.dialog.show()
            self.dialog.start()
        else:
            self.finish()

    def finish(self, *args):
        for signum, previous in self._old_signals.items():
            signal.signal(signum, previous)
        if isinstance(self.app, MeetingApplication):
            self.app.quit_requested.disconnect(self.shutdown)
        self.app.exit(self.exit_code)


def run_desktop(args, session_factory, has_transcript):
    app = QApplication.instance() or MeetingApplication([])
    app.setFont(QFont("Apple SD Gothic Neo", 12))
    app.setApplicationName("ZoomTrans")
    app.setOrganizationName("ZoomTrans")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "assets/zoomtrans.png")))
    controller = DesktopController(app, args, session_factory, has_transcript)
    QTimer.singleShot(0, controller.start)
    app.exec()
    return controller.exit_code
