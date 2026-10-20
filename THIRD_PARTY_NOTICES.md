# Third-party software

ZoomTrans's own code is MIT licensed. This does not replace the licenses of
third-party libraries, drivers, or AI models.

The downloadable app includes Python, PySide6/Qt, MLX, mlx-whisper, NumPy,
SciPy, Numba/LLVM, PortAudio/sounddevice, and their runtime dependencies.
Original license files and a build dependency inventory are provided under
`zoomtrans.app/Contents/Resources/licenses`. Some build-only or uncollected
dependencies may also appear in that inventory.

## Qt / PySide6

PySide6 6.8.3 and the Qt modules used by this app are distributed under LGPLv3
and their applicable third-party licenses. Qt is dynamically linked. You may
modify or replace these libraries, including for debugging modifications;
ZoomTrans imposes no additional restriction on reverse engineering for that
purpose. A modified bundle may need a new ad-hoc signature.

- License terms: https://www.gnu.org/licenses/lgpl-3.0.html
- Corresponding Qt 6.8.3 source: https://download.qt.io/archive/qt/6.8/6.8.3/single/
- Corresponding PySide6 6.8.3 source: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/
- Rebuild instructions: https://doc.qt.io/qtforpython-6.8/building_from_source/index.html
- Application build instructions: docs/development.md

## External components

BlackHole, the Ollama server, and model weights are not included in the app.
Install them from their respective publishers and observe their terms.

- BlackHole: https://github.com/ExistentialAudio/BlackHole
- Ollama: https://github.com/ollama/ollama
- Whisper MLX model: https://huggingface.co/mlx-community/whisper-large-v3-mlx
- Default translation model: https://ollama.com/library/gemma4

Model downloads can be large. Initial setup contacts the model publishers;
normal speech recognition and translation run locally after setup.
