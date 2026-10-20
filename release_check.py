"""Explicit synthetic-audio inference check; never starts capture."""
import json
from pathlib import Path
import queue
import wave

import numpy as np
from logger import TranscriptLogger
from messages import SubtitleEvent
from transcriber import mlx_whisper, MLX_MODELS
from translator import Translator


def run_inference_check(args):
    with wave.open(args.self_test, "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("Self-test requires a mono 16-bit 16000 Hz synthetic WAV")
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
    result = mlx_whisper.transcribe(samples, path_or_hf_repo=MLX_MODELS[args.whisper_model],
                                    language="en", word_timestamps=True, verbose=False)
    text = result["text"].strip()
    if not text:
        raise RuntimeError("Self-test produced empty transcription")
    worker = Translator(args.model, queue.Queue(), queue.Queue(),
                        complex_model=args.complex_model)
    try:
        event = SubtitleEvent("en_only", "synthetic-release-check", 1, text)
        korean = worker._translate(event)
        if not korean or not any("가" <= char <= "힣" for char in korean):
            raise RuntimeError("Self-test did not produce Korean")
        root = Path(args.log_dir)
        root.mkdir(parents=True, exist_ok=True)
        jobs = queue.Queue()
        logger = TranscriptLogger(jobs, str(root / "synthetic-self-test.txt"))
        logger.start()
        from dataclasses import replace
        jobs.put(event)
        jobs.put(replace(event, kind="en_ko", translation=korean))
        logger.stop()
        if not logger.error_queue.empty():
            raise RuntimeError(logger.error_queue.get_nowait())
        print(json.dumps({"ok": True, "original": text, "translation": korean,
                          "model": worker._last_model,
                          "word_timestamps": any(s.get("words") for s in result["segments"])},
                         ensure_ascii=False))
        return 0
    finally:
        worker._client._client.close()


def run_ui_check(path):
    from PySide6.QtCore import QTimer, QSettings
    from PySide6.QtWidgets import QApplication
    from overlay import SubtitleOverlay
    from ui_settings import PresentationSettings
    from ui_widgets import apply_application_theme
    from setup_dialog import SetupDialog
    from setup_checks import SetupState
    from types import SimpleNamespace
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        app = QApplication.instance() or QApplication([])
        apply_application_theme(app)
        preferences = PresentationSettings(QSettings(str(Path(temporary) / "settings.ini"),
                                                       QSettings.Format.IniFormat))
        window = SubtitleOverlay(queue.Queue(), preferences)
        window.subtitle_queue.put(SubtitleEvent("en_only", "synthetic-ui-check", 1,
                                               "Please keep the backup server online."))
        window.show()
        def capture():
            window._poll()
            if not window.grab().save(path):
                app.exit(1)
                return
            window.stop()
            window.close()
            setup = SetupDialog(SimpleNamespace(whisper_model="large-v3"))
            setup._checked(SetupState(("BlackHole 16ch를 설치한 뒤 다시 확인해 주세요.",
                                       "Ollama 앱을 실행해 주세요.",
                                       "Whisper 모델을 다운로드해 주세요."), True))
            setup.show()
            app.processEvents()
            target = Path(path)
            if not setup.grab().save(str(target.with_stem(target.stem + "-setup"))):
                app.exit(1)
                return
            setup.hide()
            app.exit(0)
        QTimer.singleShot(500, capture)
        return app.exec()
