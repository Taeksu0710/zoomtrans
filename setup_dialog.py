"""Non-blocking prerequisite preparation before starting any audio worker."""
import threading
from PySide6.QtCore import Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QVBoxLayout
from ui_widgets import button, label
from setup_checks import check_setup, download_whisper


class SetupDialog(QDialog):
    ready = Signal()
    cancelled = Signal()
    checked = Signal(object)
    downloaded = Signal(str)

    def __init__(self, args):
        super().__init__()
        self.args = args
        self._busy = False
        self.setWindowTitle("ZoomTrans · 시작 준비")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(label("첫 실행에 필요한 항목을 확인합니다."))
        self.message = label("확인 중…", True)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.download_button = button("Whisper 모델 다운로드", self.download)
        self.download_button.hide()
        layout.addWidget(self.download_button)
        layout.addWidget(label("모델 다운로드에는 인터넷과 수 GB의 여유 공간이 필요합니다. "
                               "모델을 준비한 뒤 인식과 번역은 로컬에서 처리합니다.", True))
        self.help_button = button("설치 안내", lambda: QDesktopServices.openUrl(QUrl(
            "https://github.com/Taeksu0710/zoomtrans/blob/main/docs/installation.md")))
        layout.addWidget(self.help_button)
        self.retry_button = button("다시 확인", self.check, primary=True)
        layout.addWidget(self.retry_button)
        self.checked.connect(self._checked)
        self.downloaded.connect(self._downloaded)

    def _set_busy(self, value):
        self._busy = value
        self.retry_button.setEnabled(not value)
        self.download_button.setEnabled(not value)

    def check(self):
        if self._busy:
            return
        self._set_busy(True)
        self.message.setText("설치 상태 확인 중…")
        def worker():
            try:
                self.checked.emit(check_setup(self.args))
            except Exception as exc:
                self.checked.emit(str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def _checked(self, result):
        self._set_busy(False)
        if isinstance(result, str):
            self.message.setText(f"설치 상태 확인 실패: {result}")
            return
        if not result.issues:
            self.hide()
            self.ready.emit()
            return
        self.message.setText("\n\n".join(result.issues))
        self.download_button.setVisible(result.whisper_missing)

    def download(self):
        if self._busy:
            return
        self._set_busy(True)
        self.message.setText("Whisper 모델 다운로드 중… 완료될 때까지 창을 유지해 주세요.")
        def worker():
            try:
                download_whisper(self.args.whisper_model)
                self.downloaded.emit("")
            except Exception as exc:
                self.downloaded.emit(str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def _downloaded(self, error):
        self._set_busy(False)
        if error:
            self.message.setText(f"다운로드 실패: {error}\n다시 시도할 수 있습니다.")
        else:
            self.check()

    def reject(self):
        if not self._busy:
            self.cancelled.emit()
            super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)
