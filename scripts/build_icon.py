#!/usr/bin/env python3
"""Render the canonical vector icon and install the macOS app-bundle icon."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import plistlib
import shutil
import subprocess
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    root = Path(__file__).resolve().parents[1]
    renderer = QSvgRenderer(str(root / "assets/zoomtrans.svg"))
    if not renderer.isValid():
        raise RuntimeError("Invalid icon SVG")
    iconset = root / "output/icon/ZoomTrans.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = size * scale
            bitmap = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
            bitmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(bitmap)
            renderer.render(painter)
            painter.end()
            path = iconset / f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            if not bitmap.save(str(path)):
                raise RuntimeError(f"Cannot save {path}")
            if pixels == 1024:
                bitmap.save(str(root / "assets/zoomtrans.png"))
    destination = root / "assets/zoomtrans.icns"
    subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(destination)], check=True)
    bundle = root / "zoomtrans.app/Contents"
    if not bundle.is_dir():
        print(f"Icons generated: {destination}")
        return
    resources = bundle / "Resources"
    resources.mkdir(exist_ok=True)
    shutil.copy2(destination, resources / "ZoomTrans.icns")
    plist_path = bundle / "Info.plist"
    with plist_path.open("rb") as file:
        info = plistlib.load(file)
    info["CFBundleIconFile"] = "ZoomTrans.icns"
    with plist_path.open("wb") as file:
        plistlib.dump(info, file)
    os.utime(root / "zoomtrans.app", None)
    print(f"Icon installed: {resources / 'ZoomTrans.icns'}")


if __name__ == "__main__":
    main()
