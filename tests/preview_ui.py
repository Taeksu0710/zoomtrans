"""Render deterministic screenshots, without audio devices or model services.

Run: .venv/bin/python -B -m unittest discover -s tests -p preview_ui.py
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import queue
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from messages import RuntimeStatus, SubtitleEvent
from overlay import SubtitleOverlay
from ui_settings import PresentationSettings


class Preview(unittest.TestCase):
    def test_render(self):
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        app.setFont(QFont("Apple SD Gothic Neo", 12))
        light_palette = QPalette(app.palette())
        for role, color in (
            (QPalette.ColorRole.Window, "#f8f9fb"),
            (QPalette.ColorRole.WindowText, "#202631"),
            (QPalette.ColorRole.Base, "#ffffff"),
            (QPalette.ColorRole.Text, "#202631"),
            (QPalette.ColorRole.Button, "#f8f9fb"),
            (QPalette.ColorRole.ButtonText, "#202631"),
        ):
            light_palette.setColor(role, QColor(color))
        app.setPalette(light_palette)
        output = Path(__file__).resolve().parents[1] / "output" / "ui"
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as d:
            preferences = PresentationSettings(
                QSettings(str(Path(d) / "prefs.ini"), QSettings.Format.IniFormat),
                Path(d) / "absent.json",
            )
            window = SubtitleOverlay(queue.Queue(), preferences)
            window.show()
            for component in ("input", "stt", "translator", "logger"):
                window.subtitle_queue.put(RuntimeStatus(component, "ready"))
            window.subtitle_queue.put(RuntimeStatus("input", "level", level=0.06))
            first = SubtitleEvent(
                "en_ko", "first", 1,
                "Let's keep the first release focused on the essentials.",
                "첫 번째 버전은 꼭 필요한 기능에 집중하면 좋겠습니다.",
            )
            current = SubtitleEvent(
                "en_ko", "current", 1,
                "We should review the schedule before Friday.",
                "금요일 전에 일정을 검토하면 좋겠습니다.",
            )
            window.subtitle_queue.put(first)
            window.subtitle_queue.put(current)
            window._poll()

            def save(name):
                app.processEvents()
                QTest.qWait(30)
                app.processEvents()
                self.assertTrue(window.grab().save(str(output / name)))

            save("compact-light.png")
            window.subtitle_queue.put(RuntimeStatus("translator", "busy_complex", "대기 2문장"))
            window._poll()
            save("complex-translating-light.png")
            window.subtitle_queue.put(RuntimeStatus("translator", "ready"))
            window._poll()
            window.freeze_button.click()
            window.subtitle_queue.put(SubtitleEvent("en_only", "new", 1, "Another point to consider."))
            window._poll()
            save("frozen-light.png")
            window.freeze_button.click()
            window.toggle_history()
            for i in range(3):
                item = SubtitleEvent(
                    "en_ko", f"history-{i}", 1,
                    ("The design review is scheduled for Thursday." if i != 1 else
                     "Please share the updated plan with the team."),
                    ("디자인 검토는 목요일로 예정되어 있습니다." if i != 1 else
                     "업데이트된 계획을 팀에 공유해 주세요."),
                )
                window.subtitle_queue.put(item)
            window._poll()
            save("history-light.png")
            window.toggle_history()
            # Restore the two representative rows for direct theme comparison.
            window.store.pairs = []
            window.store.apply(first)
            window.store.apply(replace(current, kind="en_ko"))
            window._render()
            window.open_settings()
            app.processEvents()
            self.assertTrue(window._settings_dialog.grab().save(str(output / "settings-light.png")))
            window._settings_dialog.complex_check.setChecked(True)
            app.processEvents()
            self.assertTrue(window._settings_dialog.grab().save(str(output / "settings-12b-on-light.png")))
            window._settings_dialog.hide()
            palette = QPalette(app.palette())
            palette.setColor(QPalette.ColorRole.Window, QColor("#1b1f25"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#edf0f5"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#20242b"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#edf0f5"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#20242b"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#edf0f5"))
            app.setPalette(palette)
            save("compact-dark.png")
            window.stop()
            window.deleteLater()
            app.processEvents()
        print(f"Rendered previews: {output}")
