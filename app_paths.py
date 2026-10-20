"""Read-only bundle resources and writable user data have separate roots."""
from pathlib import Path
import sys

VERSION = "0.1.0-preview.1"


def bundled():
    return bool(getattr(sys, "frozen", False))


def resource_path(relative):
    return Path(__file__).resolve().parent / relative


def default_log_dir():
    if bundled():
        return str(Path.home() / "Library/Application Support/ZoomTrans/logs")
    return "logs"
