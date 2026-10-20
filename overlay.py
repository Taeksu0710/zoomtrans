"""Qt subtitle panel. All mutation of widgets stays on the GUI thread."""

import math
import queue
import time

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMenuBar,
    QLayout, QScrollArea, QSizeGrip, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from subtitle_model import SubtitleStore
from messages import SubtitleEvent
from performance import record
from ui_settings import PresentationSettings, clamp_geometry
from ui_widgets import apply_application_theme, button, label, theme_colors


class InputMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.level = 0.0
        self.setFixedSize(24, 16)
        self.setAccessibleName("오디오 입력 신호")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme_colors()
        active = math.ceil(min(1.0, self.level * 8) * 4)
        painter.setPen(Qt.PenStyle.NoPen)
        for i, height in enumerate((5, 9, 14, 8)):
            painter.setBrush(QColor(c["blue"] if i < active else c["line"]))
            painter.drawRoundedRect(QRectF(i * 6, (16 - height) / 2, 3, height), 1.5, 1.5)


class DragHeader(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self._offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle and handle.startSystemMove():
                return
            self._offset = event.globalPosition().toPoint() - self.window().pos()

    def mouseMoveEvent(self, event):
        if self._offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._offset)

    def mouseReleaseEvent(self, event):
        self._offset = None


class SubtitleRow(QWidget):
    def __init__(self):
        super().__init__()
        # Minimum would use the label's narrow initial sizeHint as its minimum
        # height, leaving a large blank scroll region after text wraps wider.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(5)
        self.timestamp = label(secondary=True)
        self.original = label(secondary=True)
        self.translation = label()
        self.error = label()
        for widget in (self.timestamp, self.original, self.translation, self.error):
            widget.setTextFormat(Qt.TextFormat.PlainText)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            widget.setMinimumWidth(0)
            layout.addWidget(widget)

    def update_pair(self, pair, preferences, history, previous=False):
        c = theme_colors()
        self.timestamp.setVisible(history)
        self.timestamp.setText(pair["timestamp"].strftime("%H:%M:%S"))
        self.timestamp.setFont(QFont("Apple SD Gothic Neo", 10))
        self.original.setVisible(bool(pair["en"]) and preferences.show_original and not previous)
        self.original.setText(pair["en"] or "")
        self.original.setFont(QFont("Apple SD Gothic Neo", 12))
        korean = pair["ko"]
        if korean:
            text = korean + ("  ▋" if pair["streaming"] else "")
        elif pair["state"] == "pending":
            text = ("번역을 기다리고 있습니다…" if pair.get("final", True)
                    else "문장이 끝나기를 기다리고 있습니다…")
        elif pair["state"] == "skipped":
            text = "이 발화는 원문으로 기록되었습니다."
        else:
            text = ""
        self.translation.setText(text)
        self.translation.setVisible(bool(text))
        size = min(17, preferences.font_size) if previous else preferences.font_size
        self.translation.setFont(QFont("Apple SD Gothic Neo", size, QFont.Weight.DemiBold))
        color = c["muted"] if previous or not korean else c["text"]
        self.translation.setStyleSheet(f"color: {color};")
        self.error.setVisible(bool(pair["error"]))
        self.error.setText(pair["error"])
        self.error.setFont(QFont("Apple SD Gothic Neo", 11))
        self.error.setStyleSheet(f"color: {c['error']};")


class SettingsDialog(QDialog):
    changed = Signal()
    complex_changed = Signal(bool)

    def __init__(self, preferences, parent=None, complex_enabled=False, complex_model="gemma4:12b"):
        super().__init__(parent)
        self.preferences = preferences
        self.setWindowTitle("자막 설정")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        title = label("편하게 읽는 자막")
        title.setFont(QFont("Apple SD Gothic Neo", 18, QFont.Weight.DemiBold))
        layout.addWidget(title)
        self.font_label = label()
        layout.addWidget(self.font_label)
        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setRange(16, 32)
        self.font_slider.setValue(preferences.font_size)
        self.font_slider.setAccessibleName("한국어 글자 크기")
        layout.addWidget(self.font_slider)
        self.opacity_label = label()
        layout.addWidget(self.opacity_label)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(65, 100)
        self.opacity_slider.setValue(preferences.opacity)
        self.opacity_slider.setAccessibleName("배경 불투명도")
        layout.addWidget(self.opacity_slider)
        self.original_check = QCheckBox("영어 원문 함께 표시")
        self.original_check.setChecked(preferences.show_original)
        layout.addWidget(self.original_check)
        self.top_check = QCheckBox("다른 창 위에 표시")
        self.top_check.setChecked(preferences.always_on_top)
        layout.addWidget(self.top_check)
        layout.addWidget(label("화면 설정은 바로 적용되고 다음 실행에도 유지됩니다.", True))
        self.complex_check = QCheckBox(
            "긴 문장·복합 조건문에 12B 사용" if complex_model == "gemma4:12b"
            else f"긴 문장·복합 조건문에 {complex_model} 사용")
        self.complex_check.setChecked(complex_enabled)
        layout.addWidget(self.complex_check)
        description = label(
            "40단어 이상이거나 조건과 시간 표현이 함께 있는 문장에 적용합니다. "
            "번역 시간이 늘어날 수 있으며, 처음 사용할 때 모델 준비 시간이 필요합니다.\n"
            "다음 번역부터 적용되며, 진행 중인 번역은 그대로 완료됩니다. "
            "이 옵션은 이번 실행에만 적용되고 다음 실행에는 꺼집니다.", True)
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addWidget(button("완료", self.accept, primary=True))
        self.font_slider.valueChanged.connect(lambda value: self._change("font_size", value))
        self.opacity_slider.valueChanged.connect(lambda value: self._change("opacity", value))
        self.original_check.toggled.connect(lambda value: self._change("show_original", value))
        self.top_check.toggled.connect(lambda value: self._change("always_on_top", value))
        self.complex_check.toggled.connect(self.complex_changed.emit)
        self._labels()

    def _labels(self):
        self.font_label.setText(f"한국어 글자 크기    {self.preferences.font_size} pt")
        self.opacity_label.setText(f"배경 불투명도    {self.preferences.opacity}%")

    def _change(self, key, value):
        self.preferences.update(key, value)
        self._labels()
        self.changed.emit()


class SubtitleOverlay(QWidget):
    shutdown_requested = Signal()
    complex_changed = Signal(bool)

    def __init__(self, subtitle_queue, preferences=None, performance=None):
        super().__init__()
        self.performance = performance
        self.subtitle_queue = subtitle_queue
        self.preferences = preferences if preferences is not None else PresentationSettings()
        self.store = SubtitleStore()
        self.mode = "compact"
        self._follow_live = True
        self._rendering = False
        self._closing = False
        self._stopped = False
        self._rows = {}
        self._resize_origin = None
        self._resize_edges_active = Qt.Edge(0)
        self._started_at = time.monotonic()
        self._settings_dialog = None
        self.complex_enabled = False
        self.complex_model = "gemma4:12b"
        self.setWindowTitle("ZoomTrans · 실시간 자막")
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if self.preferences.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(480, 180)
        self.setMouseTracking(True)
        self._build()
        self._make_actions()
        self.setGeometry(self.preferences.geometry(self.mode, self._screens()))
        self._theme_changed()
        QApplication.instance().paletteChanged.connect(self._theme_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._render()
        self._refresh_status()

    def _screens(self):
        return [s.availableGeometry() for s in QApplication.screens()]

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(8)
        self.header = DragHeader(self)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = label("ZoomTrans")
        title.setFont(QFont("Apple SD Gothic Neo", 12, QFont.Weight.DemiBold))
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(title)
        self.meter = InputMeter()
        self.meter.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(self.meter)
        self.input_label = label("입력 준비 중", True)
        self.input_label.setWordWrap(False)
        self.input_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.input_label.setFont(QFont("Apple SD Gothic Neo", 10))
        header.addWidget(self.input_label)
        header.addStretch()
        self.history_button = button("기록", self.toggle_history)
        self.history_button.setCheckable(True)
        self.history_button.setToolTip("기록 펼치기 · ⌘⇧H")
        header.addWidget(self.history_button)
        self.settings_button = button("설정", self.open_settings)
        self.settings_button.setToolTip("자막 설정 · ⌘,")
        header.addWidget(self.settings_button)
        self.end_button = button("종료", self.request_shutdown)
        self.end_button.setToolTip("남은 발화와 회의록을 저장하고 종료 · ⌘Q")
        header.addWidget(self.end_button)
        layout.addWidget(self.header)
        self.warning_label = label()
        self.warning_label.setFont(QFont("Apple SD Gothic Neo", 11))
        self.warning_label.hide()
        layout.addWidget(self.warning_label)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: 0; }")
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setObjectName("subtitleContent")
        self.content.setStyleSheet("QWidget#subtitleContent { background: transparent; }")
        self.content.setAutoFillBackground(False)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 4, 8, 4)
        self.content_layout.setSpacing(14)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.scroll.setWidget(self.content)
        self.scroll.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._range_changed)
        layout.addWidget(self.scroll, 1)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.state_label = label("모델 준비 중…", True)
        self.state_label.setFont(QFont("Apple SD Gothic Neo", 10))
        self.state_label.setWordWrap(False)
        footer.addWidget(self.state_label, 1)
        self.unseen_label = label(secondary=True)
        self.unseen_label.setFont(QFont("Apple SD Gothic Neo", 10))
        footer.addWidget(self.unseen_label)
        self.latest_button = button("최신으로", self.go_latest, primary=True)
        self.latest_button.hide()
        footer.addWidget(self.latest_button)
        self.freeze_button = button("화면 고정")
        self.freeze_button.setCheckable(True)
        self.freeze_button.toggled.connect(self.freeze_view)
        self.freeze_button.setToolTip("표시만 고정합니다. 음성인식·번역·저장은 계속됩니다. · ⌘⇧F")
        footer.addWidget(self.freeze_button)
        self.elapsed_label = label("00:00", True)
        self.elapsed_label.setFont(QFont("Apple SD Gothic Neo", 10))
        footer.addWidget(self.elapsed_label)
        layout.addLayout(footer)
        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(12, 12)

    def _make_actions(self):
        self.shortcuts = []
        for sequence, callback in (
            ("Ctrl+Shift+F", self.freeze_button.click),
            ("Ctrl+Shift+H", self.toggle_history),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        # Give the macOS application menu the same shutdown path as the panel.
        self.menu_bar = QMenuBar(self)
        menu = self.menu_bar.addMenu("ZoomTrans")
        self.preferences_action = QAction("자막 설정…", self)
        self.preferences_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        self.preferences_action.setShortcut(QKeySequence("Ctrl+,"))
        self.preferences_action.triggered.connect(self.open_settings)
        menu.addAction(self.preferences_action)
        self.quit_action = QAction("ZoomTrans 종료", self)
        self.quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        self.quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.quit_action.triggered.connect(self.request_shutdown)
        menu.addAction(self.quit_action)
        self.addAction(self.preferences_action)
        self.addAction(self.quit_action)
        if not self.menu_bar.isNativeMenuBar():
            self.menu_bar.hide()

    def _theme_changed(self, *args):
        # Reapplying a stylesheet can itself cause a palette notification.
        if getattr(self, "_updating_theme", False):
            return
        self._updating_theme = True
        try:
            apply_application_theme(QApplication.instance())
            self.warning_label.setStyleSheet(f"color: {theme_colors()['error']};")
            self._render()
            self.update()
        finally:
            self._updating_theme = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = theme_colors()
        background = QColor(colors["surface"])
        background.setAlphaF(self.preferences.opacity / 100)
        painter.setBrush(background)
        painter.setPen(QPen(QColor(colors["line"]), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        painter.setPen(QPen(QColor(colors["line"]), 1))
        painter.drawLine(20, self.header.geometry().bottom() + 4, self.width() - 20,
                         self.header.geometry().bottom() + 4)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.grip.move(self.width() - 16, self.height() - 16)
        self.update()

    def _edge_at(self, pos):
        edges = Qt.Edge(0)
        if pos.x() < 7:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - 7:
            edges |= Qt.Edge.RightEdge
        if pos.y() < 7:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - 7:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edge_at(event.position())
            if edges:
                handle = self.windowHandle()
                if not handle or not handle.startSystemResize(edges):
                    self._resize_origin = (self.geometry(), event.globalPosition().toPoint())
                    self._resize_edges_active = edges
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_origin is not None:
            rect, start = self._resize_origin
            delta = event.globalPosition().toPoint() - start
            result = QRect(rect)
            edges = self._resize_edges_active
            if edges & Qt.Edge.LeftEdge:
                result.setLeft(min(rect.left() + delta.x(), rect.right() - self.minimumWidth() + 1))
            if edges & Qt.Edge.RightEdge:
                result.setRight(max(rect.right() + delta.x(), rect.left() + self.minimumWidth() - 1))
            if edges & Qt.Edge.TopEdge:
                result.setTop(min(rect.top() + delta.y(), rect.bottom() - self.minimumHeight() + 1))
            if edges & Qt.Edge.BottomEdge:
                result.setBottom(max(rect.bottom() + delta.y(), rect.top() + self.minimumHeight() - 1))
            self.setGeometry(result)
            return
        edges = self._edge_at(event.position())
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        cursor = Qt.CursorShape.ArrowCursor
        if horizontal and vertical:
            cursor = (Qt.CursorShape.SizeFDiagCursor if edges in (
                Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge
            ) else Qt.CursorShape.SizeBDiagCursor)
        elif horizontal:
            cursor = Qt.CursorShape.SizeHorCursor
        elif vertical:
            cursor = Qt.CursorShape.SizeVerCursor
        self.setCursor(cursor)

    def mouseReleaseEvent(self, event):
        self._resize_origin = None
        self._resize_edges_active = Qt.Edge(0)
        self.preferences.save_geometry(self.mode, self.geometry())

    def _scrolled(self):
        if not self._rendering and self.mode == "history":
            bar = self.scroll.verticalScrollBar()
            self._follow_live = bar.maximum() - bar.value() < 8
            self.latest_button.setVisible(not self._follow_live)

    def _range_changed(self, minimum, maximum):
        if self._follow_live and not self.store.frozen:
            bar = self.scroll.verticalScrollBar()
            bar.blockSignals(True)
            bar.setValue(maximum)
            bar.blockSignals(False)

    def _render(self):
        if not hasattr(self, "content_layout"):
            return
        self._rendering = True
        bar = self.scroll.verticalScrollBar()
        position = bar.value()
        anchor = next(((key, position - row.y()) for key, row in self._rows.items()
                       if row.y() <= position < row.y() + row.height()), None)
        pairs = self.store.visible_pairs
        if self.mode == "compact":
            pairs = pairs[-2:]
        keys = [p["id"] for p in pairs] or ["__empty__"]
        if list(self._rows) != keys:
            while self.content_layout.count():
                item = self.content_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._rows = {}
            for key in keys:
                row = label("대화가 시작되면 여기에 자막이 표시됩니다.", True) if key == "__empty__" else SubtitleRow()
                self._rows[key] = row
                self.content_layout.addWidget(row)
        for i, pair in enumerate(pairs):
            self._rows[pair["id"]].update_pair(
                pair, self.preferences, self.mode == "history",
                previous=self.mode == "compact" and i < len(pairs) - 1,
            )
        self.content_layout.activate()

        def restore_scroll():
            if self._stopped:
                return
            self._rendering = True
            if self._follow_live and not self.store.frozen:
                bar.setValue(bar.maximum())
            elif anchor and anchor[0] in self._rows:
                bar.setValue(self._rows[anchor[0]].y() + anchor[1])
            else:
                bar.setValue(position)
            self._rendering = False

        QTimer.singleShot(0, self, restore_scroll)

    def _refresh_status(self):
        now = time.monotonic()
        runtime = self.store.runtime
        input_status = runtime.get("input")
        level = self.store.active_input_level(now)
        self.meter.level = level
        self.meter.update()
        self.input_label.setText(
            "입력 감지" if level > 0.003 else
            ("입력 대기" if input_status and input_status.state == "ready" else "입력 준비 중")
        )
        stt, translator, logger = (runtime.get(k) for k in ("stt", "translator", "logger"))
        if stt is None or stt.state in {"loading", "calibrating"}:
            state = "음성인식 준비 중…" if not stt or stt.state == "loading" else "입력 레벨 측정 중…"
        elif stt.state == "error":
            state = "음성인식 오류"
        elif translator and translator.state == "error":
            state = "번역 오류 · 원문 기록 중"
        elif not translator or translator.state == "loading":
            state = "번역 모델 준비 중…"
        elif translator.state in {"busy", "busy_complex"}:
            state = "긴 문장 번역 중…" if translator.state == "busy_complex" else "번역 중…"
            state += f" · {translator.detail}" if translator.detail else ""
        elif stt.state == "waiting":
            state = "다음 구절 대기 중…"
        elif stt.state == "collecting":
            state = "문장 수집 중…"
        else:
            state = "대화 수신 중"
        if logger and logger.state == "ready":
            state += " · 기록 중"
        if logger and logger.state == "error":
            state = "회의록 저장 오류"
        self.state_label.setText(state)
        self.unseen_label.setVisible(self.store.frozen)
        self.unseen_label.setText(f"새 발화 {self.store.unseen_count}")
        self.freeze_button.setText("고정 해제" if self.store.frozen else "화면 고정")
        elapsed = int(now - self._started_at)
        self.elapsed_label.setText(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        warnings = [s.detail for s in (stt, translator, logger) if s and s.state == "error" and s.detail]
        if now < self.store.warning_until:
            warnings.insert(0, self.store.warning)
        self.warning_label.setText("\n".join(warnings))
        self.warning_label.setVisible(bool(warnings))

    def _poll(self):
        if self._stopped:
            return
        updated = False
        completed = []
        try:
            for _ in range(1000):
                event = self.subtitle_queue.get_nowait()
                changed = self.store.apply(event)
                updated = changed or updated
                if changed and isinstance(event, SubtitleEvent) and event.kind == "en_ko":
                    completed.append(event)
        except queue.Empty:
            pass
        if updated and not self.store.frozen:
            self._render()
            for event in completed:
                if event.speech_ended_at:
                    record(self.performance, "ui_update", utterance_id=event.utterance_id,
                           speech_to_ui_seconds=max(0.0, time.monotonic() - event.speech_ended_at))
        self._refresh_status()

    def freeze_view(self, enabled):
        self.store.freeze(enabled)
        self.freeze_button.blockSignals(True)
        self.freeze_button.setChecked(enabled)
        self.freeze_button.blockSignals(False)
        if not enabled:
            self._follow_live = True
            self._render()
        self._refresh_status()

    def go_latest(self):
        self.freeze_view(False)
        self._follow_live = True
        self.latest_button.hide()
        self._render()

    def toggle_history(self):
        if self._closing:
            return
        self.preferences.save_geometry(self.mode, self.geometry())
        self.mode = "history" if self.mode == "compact" else "compact"
        self.history_button.setChecked(self.mode == "history")
        self.history_button.setText("접기" if self.mode == "history" else "기록")
        self.setGeometry(self.preferences.geometry(self.mode, self._screens()))
        self._follow_live = True
        self.latest_button.hide()
        self._render()

    def open_settings(self):
        if self._closing:
            return
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(
                self.preferences, self, self.complex_enabled, self.complex_model)
            self._settings_dialog.changed.connect(self._apply_preferences)
            self._settings_dialog.complex_changed.connect(self._change_complex)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def set_complex_translation(self, enabled, model="gemma4:12b"):
        self.complex_enabled = bool(enabled)
        self.complex_model = model
        if self._settings_dialog:
            check = self._settings_dialog.complex_check
            check.blockSignals(True)
            check.setChecked(self.complex_enabled)
            check.blockSignals(False)

    def _change_complex(self, enabled):
        self.complex_enabled = enabled
        self.complex_changed.emit(enabled)

    def _apply_preferences(self):
        desired = self.preferences.always_on_top
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != desired:
            geometry = self.geometry()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, desired)
            self.show()
            self.setGeometry(geometry)
            if self._settings_dialog and self._settings_dialog.isVisible():
                self._settings_dialog.raise_()
        self._render()
        self.update()

    def request_shutdown(self):
        if not self._closing:
            self._closing = True
            self.header.setEnabled(False)
            self.freeze_button.setEnabled(False)
            self.quit_action.setEnabled(False)
            self.preferences_action.setEnabled(False)
            if self._settings_dialog:
                self._settings_dialog.close()
            self.shutdown_requested.emit()

    def closeEvent(self, event):
        event.ignore()
        self.request_shutdown()

    def stop(self):
        if not self._stopped:
            self._stopped = True
            self.timer.stop()
            self.preferences.save_geometry(self.mode, self.geometry())
            self.hide()

    def ensure_on_screen(self):
        self.setGeometry(clamp_geometry(self.geometry(), self._screens()))
