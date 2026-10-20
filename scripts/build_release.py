#!/usr/bin/env python3
"""Build the portable app; publishing is a separate verified step."""
import importlib.metadata as metadata
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def collect_notices():
    destination = ROOT / "build/notices"
    destination.mkdir(parents=True, exist_ok=True)
    lines = ["# Build dependency inventory", "", "License files accompany this inventory.", ""]
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        lines.append(f"- {name}=={distribution.version}")
        for file in distribution.files or ():
            # Preserve the original license relative path, including Qt's
            # license directory and nested third-party notices.
            parts = [p.lower() for p in file.parts]
            if any(p.startswith(("license", "copying", "copyright", "notice")) for p in parts):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    target = destination / name / Path(*[p for p in file.parts if p not in ("..", ".")])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    (destination / "DEPENDENCIES.md").write_text("\n".join(lines) + "\n")
    # CPython is redistributed by PyInstaller too.
    python_license = Path(sys.base_prefix) / "lib/python3.11/LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"Missing Python license: {python_license}")
    shutil.copy2(python_license, destination / "PYTHON-LICENSE.txt")


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("Build on Apple Silicon macOS")
    if int(platform.mac_ver()[0].split(".")[0]) < 26:
        raise SystemExit("This preview requires macOS 26 or later")
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Use Python 3.11")
    (ROOT / "build").mkdir(exist_ok=True)
    collect_notices()
    subprocess.run(["/usr/bin/swiftc", str(ROOT / "audio_switch.swift"),
                    "-framework", "CoreAudio", "-framework", "Foundation", "-O",
                    "-o", str(ROOT / "build/audio_switch")], check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "packaging/zoomtrans.spec"], cwd=ROOT, check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict",
                    str(ROOT / "dist/zoomtrans.app")], check=True)
    print("Built dist/zoomtrans.app. Run scripts/verify_release.py before packaging or publishing.")


if __name__ == "__main__":
    main()
