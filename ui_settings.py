"""Persistent presentation preferences and safe multi-monitor geometry."""

import json
from pathlib import Path

from PySide6.QtCore import QRect, QSettings


def clamp_geometry(rect, screens, minimum=(480, 180)):
    screen = max(screens, key=lambda s: rect.intersected(s).width() * rect.intersected(s).height())
    width = min(max(minimum[0], rect.width()), screen.width())
    height = min(max(minimum[1], rect.height()), screen.height())
    x = min(max(rect.x(), screen.x()), screen.right() - width + 1)
    y = min(max(rect.y(), screen.y()), screen.bottom() - height + 1)
    return QRect(x, y, width, height)


class PresentationSettings:
    def __init__(self, settings=None, legacy_path=None):
        self.backend = settings if settings is not None else QSettings("ZoomTrans", "ZoomTrans")
        self.font_size = self._integer("font_size", 22, 16, 32)
        self.opacity = self._integer("opacity", 92, 65, 100)
        self.show_original = self._boolean("show_original", True)
        self.always_on_top = self._boolean("always_on_top", True)
        self._migrate(legacy_path or Path(__file__).with_name(".overlay_geom.json"))

    def _integer(self, key, default, minimum, maximum):
        try:
            return max(minimum, min(maximum, int(self.backend.value(key, default))))
        except (TypeError, ValueError):
            return default

    def _boolean(self, key, default):
        value = self.backend.value(key, default)
        if isinstance(value, bool):
            return value
        if str(value).lower() in {"true", "1"}:
            return True
        if str(value).lower() in {"false", "0"}:
            return False
        return default

    def update(self, key, value):
        if key == "font_size":
            value = max(16, min(32, int(value)))
        elif key == "opacity":
            value = max(65, min(100, int(value)))
        elif key in {"show_original", "always_on_top"}:
            value = bool(value)
        else:
            raise ValueError(f"Unknown preference: {key}")
        setattr(self, key, value)
        self.backend.setValue(key, value)
        self.backend.sync()

    def geometry(self, mode, screens):
        saved = self.backend.value(f"geometry/{mode}")
        if not isinstance(saved, QRect) or not saved.isValid():
            width, height = (720, 240) if mode == "compact" else (900, 520)
            screen = screens[0]
            saved = QRect(screen.center().x() - width // 2,
                          screen.bottom() - height - 60, width, height)
        return clamp_geometry(saved, screens)

    def save_geometry(self, mode, rect):
        self.backend.setValue(f"geometry/{mode}", rect)
        self.backend.sync()

    def _migrate(self, path):
        if self._boolean("legacy_geometry_migrated", False):
            return
        try:
            geom = json.loads(Path(path).read_text())
            if isinstance(geom, dict) and all(type(geom.get(k)) is int for k in ("x", "y", "w", "h")):
                rect = QRect(geom["x"], geom["y"], geom["w"], geom["h"])
                if rect.isValid() and not self.backend.contains("geometry/history"):
                    self.backend.setValue("geometry/history", rect)
        except (OSError, ValueError, TypeError):
            pass
        self.backend.setValue("legacy_geometry_migrated", True)
        self.backend.sync()
