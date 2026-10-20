"""Shared palette and quiet, keyboard-accessible desktop controls."""

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLabel, QPushButton


def theme_colors():
    dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
    return {
        "surface": "#20242b" if dark else "#f8f9fb",
        "text": "#edf0f5" if dark else "#202631",
        "muted": "#a7b0bf" if dark else "#657080",
        "line": "#3b424e" if dark else "#dce1e9",
        "hover": "#353e4c" if dark else "#e8edf5",
        "blue": "#8ab4ff" if dark else "#245cba",
        "blue_fill": "#2a3c59" if dark else "#e5edfc",
        "error": "#ffb1a8" if dark else "#a52d24",
    }


def apply_application_theme(app):
    c = theme_colors()
    app.setStyleSheet(f"""
        QDialog {{ background: {c['surface']}; }}
        QLabel {{ color: {c['text']}; background: transparent; }}
        QLabel[secondary="true"] {{ color: {c['muted']}; }}
        QPushButton {{
            color: {c['text']}; background: transparent; border: 1px solid transparent;
            border-radius: 7px; padding: 6px 10px; min-height: 20px;
        }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QPushButton:checked {{ background: {c['blue_fill']}; color: {c['blue']}; }}
        QPushButton:focus {{ border: 1px solid {c['blue']}; }}
        QPushButton[primary="true"] {{ background: {c['blue_fill']}; color: {c['blue']}; }}
        QPushButton:disabled {{ color: {c['muted']}; }}
        QCheckBox {{ color: {c['text']}; spacing: 9px; }}
        QSlider::groove:horizontal {{ height: 4px; background: {c['line']}; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {c['blue']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{
            width: 14px; margin: -5px 0; border-radius: 7px;
            background: {c['text']};
        }}
        QTextBrowser {{
            color: {c['text']}; background: transparent; border: 0;
            selection-background-color: {c['blue_fill']};
        }}
        QScrollBar:vertical {{ width: 6px; background: transparent; }}
        QScrollBar::handle:vertical {{ background: {c['line']}; border-radius: 3px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QToolTip {{ color: {c['text']}; background: {c['surface']}; border: 1px solid {c['line']}; }}
    """)


def label(text="", secondary=False):
    widget = QLabel(text)
    widget.setProperty("secondary", secondary)
    widget.setWordWrap(True)
    return widget


def button(text, callback=None, primary=False):
    widget = QPushButton(text)
    widget.setProperty("primary", primary)
    widget.setAutoDefault(False)
    if callback:
        widget.clicked.connect(callback)
    return widget
