"""Startup checks never download models or change audio devices."""
from dataclasses import dataclass
from pathlib import Path

from transcriber import MLX_MODELS


@dataclass(frozen=True)
class SetupState:
    issues: tuple
    whisper_missing: bool = False


def whisper_cached(model):
    from huggingface_hub import snapshot_download
    try:
        root = Path(snapshot_download(MLX_MODELS[model], local_files_only=True))
        return (root / "config.json").is_file() and any(
            (root / name).is_file() for name in ("weights.safetensors", "weights.npz"))
    except (OSError, ValueError):
        return False


def check_setup(args):
    import ollama
    import sounddevice as sd
    issues = []
    if args.device.lower() == "system":
        try:
            if not any(d["name"] == "BlackHole 16ch" and d["max_input_channels"] > 0
                       for d in sd.query_devices()):
                issues.append("BlackHole 16ch를 설치한 뒤 다시 확인해 주세요.")
        except Exception:
            issues.append("오디오 입력 장치를 확인할 수 없습니다.")
    client = ollama.Client(timeout=3)
    try:
        names = {m.model for m in client.list().models}
        tag = args.model if ":" in args.model else args.model + ":latest"
        if tag not in names:
            issues.append(f"터미널에서 ollama pull {args.model} 명령으로 번역 모델을 준비해 주세요.")
    except Exception:
        issues.append("Ollama 앱을 설치하고 실행한 뒤 다시 확인해 주세요.")
    finally:
        transport = getattr(client, "_client", None)
        if transport:
            transport.close()
    missing = not whisper_cached(args.whisper_model)
    if missing:
        issues.append(f"Whisper {args.whisper_model} 모델이 없습니다. 아래 다운로드 버튼으로 준비할 수 있습니다.")
    return SetupState(tuple(issues), missing)


def download_whisper(model):
    # Only invoked by an explicit user action. Bypass the offline default for
    # this request without changing the process environment used by inference.
    from huggingface_hub import constants, snapshot_download
    from huggingface_hub.utils._http import reset_sessions
    previous = constants.HF_HUB_OFFLINE
    constants.HF_HUB_OFFLINE = False
    reset_sessions()
    try:
        snapshot_download(MLX_MODELS[model], allow_patterns=["config.json", "weights.safetensors", "weights.npz"])
    finally:
        constants.HF_HUB_OFFLINE = previous
        reset_sessions()
