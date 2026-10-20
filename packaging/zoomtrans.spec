# Build on macOS 26 arm64 with the isolated, locked Python 3.11 environment.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata
import importlib.util

root = Path(SPECPATH).parent
data = [(str(root / "assets"), "assets"),
        (str(root / "LICENSE"), "."),
        (str(root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(root / "packaging/licenses"), "licenses/upstream"),
        (str(root / "build/notices"), "licenses")]
for package in ("mlx", "mlx_whisper", "tiktoken"):
    data += collect_data_files(package)
data += copy_metadata("mlx-whisper", recursive=True)
binaries = [(str(root / "build/audio_switch"), ".")]
binaries += collect_dynamic_libs("mlx")
openmp = Path(importlib.util.find_spec("torch").origin).parent / "lib/libomp.dylib"
if openmp.is_file():
    binaries.append((str(openmp), "."))
a = Analysis([str(root / "main.py")], pathex=[str(root)],
             binaries=binaries, datas=data,
             hiddenimports=collect_submodules("mlx") + ["mlx_whisper", "tiktoken_ext.openai_public", "scipy.special._special_ufuncs"],
             excludes=["torch", "torchvision", "torchaudio", "tensorflow", "matplotlib",
                       "IPython", "pytest", "tkinter", "mlx_whisper.convert",
                       "mlx_whisper.torch_whisper"],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="zoomtrans",
          debug=False, strip=False, upx=False, console=False,
          target_arch="arm64", codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="zoomtrans")
app = BUNDLE(coll, name="zoomtrans.app", icon=str(root / "assets/zoomtrans.icns"),
             bundle_identifier="com.zoomtrans.app", version="0.1.0",
             info_plist={"CFBundleDisplayName": "ZoomTrans",
                         "CFBundleShortVersionString": "0.1.0",
                         "CFBundleVersion": "1",
                         "LSMinimumSystemVersion": "26.0",
                         "NSHighResolutionCapable": True,
                         "NSMicrophoneUsageDescription": "회의 오디오 입력을 자막으로 변환합니다."})
