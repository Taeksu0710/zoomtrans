#!/usr/bin/env python3
"""Relocate and test a built app, optionally running real local inference.

Only synthetic audio is generated. This script never captures system audio,
changes output devices, downloads model weights or stops another app session.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inference", action="store_true", help="Use cached Whisper and local Ollama models")
    parser.add_argument("--package", action="store_true", help="Create release ZIP after all inference checks pass")
    args = parser.parse_args()
    if args.package and not args.inference:
        parser.error("--package requires --inference")
    output = ROOT / "output/release-check"
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / "dist/zoomtrans.app"
    checks = []
    with tempfile.TemporaryDirectory(prefix="zoomtrans-portable-") as temporary:
        root = Path(temporary)
        app = root / "zoomtrans.app"
        shutil.copytree(source, app, symlinks=True)
        executable = app / "Contents/MacOS/zoomtrans"
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith(("PYTHON", "DYLD_", "QT_", "VIRTUAL_ENV"))}
        environment.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", HF_HUB_OFFLINE="1")
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            environment["QT_QPA_PLATFORM"] = "offscreen"
        def run(arguments, timeout=180):
            result = subprocess.run([str(executable), *arguments], cwd=root, env=environment,
                                    text=True, capture_output=True, timeout=timeout)
            if result.returncode:
                raise RuntimeError(f"{arguments}: {result.stdout}\n{result.stderr}")
            checks.append({"arguments": arguments, "output": result.stdout.strip()})
            print(result.stdout, flush=True)
        run(["--version"])
        run(["--help"])
        run(["--ui-smoke-test", str(output / "portable-ui.png")])
        helper = app / "Contents/Frameworks/audio_switch"
        # PyInstaller places executable resources in Frameworks and symlinks
        # them into Resources. Listing devices is read-only.
        result = subprocess.run([str(helper), "list"], capture_output=True, text=True, check=True)
        checks.append({"audio_helper_list": "ok"})
        if args.inference:
            reference = ("If we wait until the security review is complete before deploying the new release, "
                         "then we can keep the backup server online while the team checks the final report, "
                         "and we should notify the customer before Friday.")
            subprocess.run(["/usr/bin/say", "-v", "Samantha", "-r", "180", "-o", str(root / "sample.aiff"),
                            reference], check=True)
            subprocess.run(["/usr/bin/afconvert", str(root / "sample.aiff"), str(root / "sample.wav"),
                            "-f", "WAVE", "-d", "LEI16@16000", "-c", "1"], check=True)
            for model in ("none", "gemma4:12b"):
                logs = root / ("base-logs" if model == "none" else "complex-logs")
                run(["--self-test", str(root / "sample.wav"), "--complex-model", model,
                     "--log-dir", str(logs)], timeout=300)
                payload = json.loads(checks[-1]["output"].splitlines()[-1])
                expected = "gemma4:e4b" if model == "none" else model
                if payload["model"] != expected or not payload["word_timestamps"]:
                    raise RuntimeError(f"Incorrect routing/alignment: {payload}")
                if "[KO]" not in (logs / "synthetic-self-test.txt").read_text():
                    raise RuntimeError("Transcript was not saved")
    (output / "verification.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n")
    if args.package:
        import sys
        sys.path.insert(0, str(ROOT))
        from app_paths import VERSION
        archive = ROOT / "dist" / f"zoomtrans-{VERSION}-macos26-arm64.zip"
        subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        str(source), str(archive)], check=True)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        (ROOT / "dist/SHA256SUMS.txt").write_text(f"{checksum}  {archive.name}\n")
        print(f"Verified and packaged: {archive}")


if __name__ == "__main__":
    main()
