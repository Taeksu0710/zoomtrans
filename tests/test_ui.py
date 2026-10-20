"""Qt regressions. Use QT_QPA_PLATFORM=offscreen for a display-free run."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import queue
import tempfile
import threading
import time
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog

from logger import TranscriptLogger
from messages import Broadcaster, RuntimeStatus, SubtitleEvent
from overlay import SettingsDialog, SubtitleOverlay
from subtitle_model import SubtitleStore
from ui_app import ChoiceDialog, DesktopController, MeetingApplication, ProgressDialog, SummaryDialog
from ui_settings import PresentationSettings, clamp_geometry


def original(identity="a", text="We should review the schedule before Friday."):
    return SubtitleEvent("en_only", identity, 1, text)


class StoreTests(unittest.TestCase):
    def test_frozen_view_keeps_snapshot_while_model_revises_and_counts_unique_new_speech(self):
        model = SubtitleStore()
        first = original()
        model.apply(first)
        model.freeze(True)
        model.apply(replace(first, kind="en_ko", translation="수정된 번역"))
        new = original("new")
        model.apply(new)
        model.apply(replace(new, kind="ko_partial", translation="부분"))
        model.apply(replace(new, revision=2, text="Corrected"))
        self.assertIsNone(model.visible_pairs[0]["ko"])
        self.assertEqual(model.pairs[0]["ko"], "수정된 번역")
        self.assertEqual(model.unseen_count, 1)
        model.freeze(False)
        self.assertEqual(model.visible_pairs, model.pairs)
        self.assertEqual(model.unseen_count, 0)

    def test_freeze_retains_snapshot_after_live_history_is_trimmed(self):
        model = SubtitleStore()
        model.apply(original("old"))
        model.freeze(True)
        for i in range(120):
            model.apply(original(str(i)))
        self.assertEqual(model.visible_pairs[0]["id"], "old")
        self.assertEqual(len(model.pairs), 100)
        self.assertEqual(model.unseen_count, 120)
        model.freeze(False)
        self.assertNotIn("old", [p["id"] for p in model.visible_pairs])

    def test_discarded_new_preview_is_not_counted(self):
        model = SubtitleStore()
        model.freeze(True)
        preview = replace(original("new"), final=False)
        model.apply(preview)
        model.apply(replace(preview, revision=2, kind="discard"))
        self.assertEqual(model.unseen_count, 0)

    def test_freeze_does_not_stop_transcript_writes(self):
        subtitles, logs = queue.Queue(), queue.Queue()
        bus = Broadcaster(subtitles, logs)
        model = SubtitleStore()
        model.freeze(True)
        item = original("new")
        bus.put_nowait(item)
        bus.put_nowait(replace(item, kind="en_ko", translation="새 발화도 저장됩니다."))
        while not subtitles.empty():
            model.apply(subtitles.get_nowait())
        self.assertEqual(model.visible_pairs, [])
        self.assertEqual(model.unseen_count, 1)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "meeting.txt"
            logger = TranscriptLogger(logs, str(path))
            logger._stop_event.set()
            logger.start()
            logger.stop()
            self.assertIn("새 발화도 저장됩니다.", path.read_text())

    def test_runtime_messages_do_not_enter_transcript_and_input_meter_expires(self):
        subtitles, logs = queue.Queue(), queue.Queue()
        bus = Broadcaster(subtitles, logs)
        bus.put_nowait(RuntimeStatus("stt", "ready"))
        bus.put_nowait(RuntimeStatus("input", "level", level=0.2))
        model = SubtitleStore()
        while not subtitles.empty():
            model.apply(subtitles.get_nowait())
        self.assertTrue(logs.empty())
        self.assertEqual(model.runtime["stt"].state, "ready")
        self.assertEqual(model.active_input_level(), 0.2)
        self.assertEqual(model.active_input_level(model.input_updated_at + 2), 0)


class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or MeetingApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setFont(QFont("Apple SD Gothic Neo", 12))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temp.name) / "prefs.ini"
        self.preferences = PresentationSettings(
            QSettings(str(self.settings_path), QSettings.Format.IniFormat),
            Path(self.temp.name) / "absent.json",
        )
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            if isinstance(widget, SubtitleOverlay):
                widget.stop()
            widget.hide()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def window(self):
        widget = SubtitleOverlay(queue.Queue(), self.preferences)
        self.widgets.append(widget)
        widget.show()
        self.pump()
        return widget

    def pump(self):
        self.app.processEvents()
        QTest.qWait(10)
        self.app.processEvents()

    def test_compact_history_switch_restores_separate_geometries(self):
        window = self.window()
        window.setGeometry(20, 20, 600, 230)
        window.history_button.click()
        self.pump()
        self.assertEqual(window.mode, "history")
        window.setGeometry(30, 30, 700, 450)
        window.history_button.click()
        self.pump()
        self.assertEqual(window.mode, "compact")
        self.assertEqual(window.size().width(), 600)
        self.assertEqual(window.size().height(), 230)
        window.history_button.click()
        self.assertEqual(window.size().width(), 700)
        self.assertEqual(window.size().height(), 450)

    def test_latest_translation_is_inside_compact_viewport(self):
        window = self.window()
        for identity in ("first", "last"):
            item = original(identity)
            window.subtitle_queue.put(replace(item, kind="en_ko", translation="금요일 전에 일정을 검토하면 좋겠습니다."))
        window._poll()
        self.pump()
        widget = window._rows["last"].translation
        top = widget.mapTo(window.scroll.viewport(), QPoint(0, 0)).y()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top + widget.height(), window.scroll.viewport().height())
        self.assertTrue(window._rows["first"].original.isHidden())
        window.resize(520, 240)
        self.pump()
        top = widget.mapTo(window.scroll.viewport(), QPoint(0, 0)).y()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top + widget.height(), window.scroll.viewport().height())

    def test_freeze_button_preserves_visible_text_and_unfreezes_to_latest(self):
        window = self.window()
        first = original()
        window.subtitle_queue.put(replace(first, kind="en_ko", translation="이전 번역"))
        window._poll()
        window.freeze_button.click()
        window.subtitle_queue.put(replace(first, revision=2, kind="en_ko", translation="수정된 번역"))
        window.subtitle_queue.put(original("new", "A new utterance."))
        window._poll()
        self.assertEqual(window._rows["a"].translation.text(), "이전 번역")
        self.assertEqual(window.unseen_label.text(), "새 발화 1")
        window.freeze_button.click()
        self.pump()
        self.assertEqual(window._rows["a"].translation.text(), "수정된 번역")
        self.assertIn("new", window._rows)

    def test_actual_multiline_labels_are_replaced_without_duplicate_rows(self):
        window = self.window()
        first = original(text="Original\nsecond line")
        window.subtitle_queue.put(replace(first, kind="ko_partial", translation="첫\n부분"))
        window._poll()
        window.subtitle_queue.put(replace(first, kind="en_error", error="시간 초과"))
        window._poll()
        self.assertEqual(window._rows["a"].original.text(), "Original\nsecond line")
        self.assertEqual(window._rows["a"].translation.text(), "첫\n부분")
        self.assertEqual(window._rows["a"].error.text(), "시간 초과")
        window.subtitle_queue.put(replace(first, revision=2, kind="en_ko", text="Revised", translation="최종"))
        window._poll()
        self.assertEqual(list(window._rows), ["a"])
        self.assertEqual(window._rows["a"].translation.text(), "최종")
        self.assertTrue(window._rows["a"].error.isHidden())

    def test_settings_apply_immediately_and_persist_without_fading_text(self):
        window = self.window()
        item = original()
        window.subtitle_queue.put(replace(item, kind="en_ko", translation="글자 크기 확인"))
        window._poll()
        window.open_settings()
        dialog = window._settings_dialog
        dialog.font_slider.setValue(30)
        dialog.opacity_slider.setValue(70)
        dialog.original_check.setChecked(False)
        dialog.top_check.setChecked(False)
        self.pump()
        self.assertEqual(window._rows["a"].translation.font().pointSize(), 30)
        self.assertTrue(window._rows["a"].original.isHidden())
        self.assertEqual(window.windowOpacity(), 1.0)
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        loaded = PresentationSettings(QSettings(str(self.settings_path), QSettings.Format.IniFormat))
        self.assertEqual(loaded.font_size, 30)
        self.assertEqual(loaded.opacity, 70)
        self.assertFalse(loaded.show_original)
        self.assertFalse(loaded.always_on_top)

    def test_complex_toggle_is_session_only_and_reopening_keeps_state(self):
        window = self.window()
        changes = []
        window.complex_changed.connect(changes.append)
        window.open_settings()
        dialog = window._settings_dialog
        self.assertFalse(dialog.complex_check.isChecked())
        dialog.complex_check.setChecked(True)
        self.assertTrue(window.complex_enabled)
        dialog.accept()
        window.open_settings()
        self.assertTrue(dialog.complex_check.isChecked())
        dialog.font_slider.setValue(28)
        self.assertEqual(changes, [True])
        fresh = self.window()
        fresh.open_settings()
        self.assertFalse(fresh._settings_dialog.complex_check.isChecked())
        window.set_complex_translation(False)
        self.assertFalse(dialog.complex_check.isChecked())
        self.assertEqual(changes, [True])

    def test_setup_dialog_requires_ready_state_and_disables_actions_while_downloading(self):
        from setup_dialog import SetupDialog
        from setup_checks import SetupState
        dialog = SetupDialog(types.SimpleNamespace(whisper_model="large-v3"))
        self.widgets.append(dialog)
        ready = []
        dialog.ready.connect(lambda: ready.append(True))
        dialog._checked(SetupState(("Whisper 모델 없음",), True))
        self.assertFalse(dialog.download_button.isHidden())
        self.assertFalse(ready)
        dialog._set_busy(True)
        self.assertFalse(dialog.download_button.isEnabled())
        dialog._downloaded("network error")
        self.assertIn("다시 시도", dialog.message.text())
        self.assertTrue(dialog.retry_button.isEnabled())
        dialog._checked(SetupState(()))
        self.assertEqual(ready, [True])

    def test_history_scrolling_keeps_position_until_latest_is_requested(self):
        window = self.window()
        window.toggle_history()
        for i in range(20):
            item = original(str(i), f"Sentence {i} with a longer explanation.")
            window.subtitle_queue.put(replace(item, kind="en_ko", translation=f"발화 {i}의 자세한 번역 내용입니다."))
        window._poll()
        self.pump()
        bar = window.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(0)
        self.pump()
        self.assertFalse(window._follow_live)
        window.subtitle_queue.put(original("new"))
        window._poll()
        self.pump()
        self.assertLess(bar.value(), bar.maximum())
        self.assertFalse(window.latest_button.isHidden())
        window.latest_button.click()
        self.pump()
        self.assertEqual(bar.value(), bar.maximum())

    def test_readiness_and_input_are_based_on_real_messages(self):
        window = self.window()
        self.assertIn("준비", window.state_label.text())
        for message in (
            RuntimeStatus("stt", "ready"), RuntimeStatus("translator", "ready"),
            RuntimeStatus("logger", "ready"), RuntimeStatus("input", "ready"),
            RuntimeStatus("input", "level", level=0.1),
        ):
            window.subtitle_queue.put(message)
        window._poll()
        self.assertEqual(window.input_label.text(), "입력 감지")
        self.assertEqual(window.state_label.text(), "대화 수신 중 · 기록 중")
        for status, expected in ((RuntimeStatus("stt", "collecting"), "문장 수집"),
                                 (RuntimeStatus("stt", "waiting"), "다음 구절"),
                                 (RuntimeStatus("translator", "busy_complex", "대기 2문장"), "긴 문장 번역 중"),
                                 (RuntimeStatus("translator", "busy", "대기 3문장"), "대기 3문장")):
            window.subtitle_queue.put(status)
            window._poll()
            self.assertIn(expected, window.state_label.text())
        window.subtitle_queue.put(RuntimeStatus("translator", "error", "서버에 연결할 수 없습니다."))
        window._poll()
        self.assertIn("번역 오류", window.state_label.text())
        self.assertIn("서버에 연결", window.warning_label.text())
        self.assertFalse(window.warning_label.isHidden())

    def test_ui_latency_is_recorded_for_updates_but_not_while_frozen(self):
        window = self.window()
        window.performance = Mock()
        first = replace(original(), kind="en_ko", translation="완료된 번역",
                        speech_ended_at=time.monotonic() - 1)
        window.subtitle_queue.put(first)
        window._poll()
        self.assertEqual(window.performance.call_count, 1)
        self.assertEqual(window.performance.call_args.args, ("ui_update",))
        self.assertGreaterEqual(window.performance.call_args.kwargs["speech_to_ui_seconds"], 1)
        window.freeze_view(True)
        window.subtitle_queue.put(replace(first, utterance_id="second"))
        window._poll()
        self.assertEqual(window.performance.call_count, 1)

    def test_close_and_quit_action_request_shutdown_exactly_once(self):
        window = self.window()
        spy = QSignalSpy(window.shutdown_requested)
        window.close()
        window.quit_action.trigger()
        window.request_shutdown()
        self.assertEqual(spy.count(), 1)

    def test_preference_ranges_and_corrupt_values(self):
        settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        settings.setValue("font_size", "broken")
        settings.setValue("opacity", 500)
        settings.setValue("show_original", "false")
        loaded = PresentationSettings(settings)
        self.assertEqual(loaded.font_size, 22)
        self.assertEqual(loaded.opacity, 100)
        self.assertFalse(loaded.show_original)

    def test_legacy_geometry_is_imported_once_and_clamped_to_available_screen(self):
        path = Path(self.temp.name) / "old.json"
        path.write_text('{"x": 9000, "y": -900, "w": 900, "h": 520}')
        settings = QSettings(str(Path(self.temp.name) / "legacy.ini"), QSettings.Format.IniFormat)
        preferences = PresentationSettings(settings, path)
        screen = QRect(0, 0, 800, 600)
        rect = preferences.geometry("history", [screen])
        self.assertTrue(screen.contains(rect))
        preferences.save_geometry("history", QRect(20, 20, 600, 400))
        path.write_text('{"x": 0, "y": 0, "w": 500, "h": 300}')
        loaded = PresentationSettings(settings, path)
        self.assertEqual(loaded.geometry("history", [screen]), QRect(20, 20, 600, 400))
        second = QRect(-1280, 0, 1280, 720)
        self.assertTrue(second.contains(clamp_geometry(QRect(-1200, 20, 600, 300), [screen, second])))

    def test_choice_close_selects_default_once(self):
        dialog = ChoiceDialog("Test", "Choose", [("Yes", True), ("No", False)], False)
        self.widgets.append(dialog)
        spy = QSignalSpy(dialog.selected)
        dialog.show()
        dialog.reject()
        dialog.reject()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0), [False])

    def test_progress_window_cannot_abandon_shutdown(self):
        dialog = ProgressDialog("Saving", "Saving remaining speech")
        self.widgets.append(dialog)
        dialog.show()
        dialog.reject()
        self.assertTrue(dialog.isVisible())

    def test_native_quit_event_is_routed_to_shutdown_signal(self):
        spy = QSignalSpy(self.app.quit_requested)
        QCoreApplication.sendEvent(self.app, QEvent(QEvent.Type.Quit))
        self.assertEqual(spy.count(), 1)

    def test_summary_is_saved_before_close_is_enabled(self):
        path = Path(self.temp.name) / "meeting.txt"
        path.write_text("[EN] Meeting")
        entered, release = threading.Event(), threading.Event()

        def summarize(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test worker timed out")
            return "## 회의 개요\n테스트 회의 요약입니다."

        dialog = SummaryDialog(str(path), "test")
        self.widgets.append(dialog)
        with patch("ui_app.MeetingSummarizer") as summarizer:
            summarizer.return_value.summarize_file.side_effect = summarize
            dialog.show()
            dialog.start()
            self.assertTrue(entered.wait(3))
            dialog.reject()
            self.assertTrue(dialog.isVisible())
            self.assertFalse(dialog.close_button.isEnabled())
            release.set()
            dialog._worker_thread.join(3)
            self.pump()
        self.assertTrue(dialog.close_button.isEnabled())
        self.assertIn("회의 요약", path.with_name("meeting_summary.txt").read_text())
        self.assertIn("회의 개요", dialog.browser.toPlainText())

    def test_controller_worker_failure_reaches_error_dialog_after_cleanup(self):
        session = types.SimpleNamespace(
            subtitle_queue=queue.Queue(), error_queue=queue.Queue(),
            translator=Mock(complex_enabled=True, complex_model="gemma4:12b"),
            start=Mock(), stop=Mock(return_value=[]), log_filename="unused.txt", model="test",
        )
        args = types.SimpleNamespace(device="BlackHole", silent=False)
        controller = DesktopController(self.app, args, lambda *args: session, lambda path: False)
        with patch("ui_app.SubtitleOverlay", side_effect=lambda q, **kw: SubtitleOverlay(q, self.preferences, **kw)):
            controller._choose_mode()
        self.widgets.append(controller.overlay)
        controller.overlay.open_settings()
        self.assertTrue(controller.overlay._settings_dialog.complex_check.isChecked())
        controller.overlay._settings_dialog.complex_check.setChecked(False)
        session.translator.set_complex_enabled.assert_called_once_with(False)
        session.error_queue.put("model missing offline")
        controller._poll_errors()
        controller._shutdown_thread.join(3)
        self.pump()
        self.widgets.append(controller.dialog)
        self.assertEqual(controller.exit_code, 1)
        self.assertTrue(controller.dialog.isVisible())
        self.assertFalse(isinstance(controller.dialog, ProgressDialog))
        session.stop.assert_called_once()
        self.assertEqual(controller.overlay.timer.isActive(), False)
        controller.finish()


if __name__ == "__main__":
    unittest.main()
