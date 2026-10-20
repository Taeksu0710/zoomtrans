# 개발·테스트·릴리스

## 깨끗한 환경

Apple Silicon macOS 26 이상, Python 3.11, Xcode Command Line Tools가 필요합니다.
현재 Preview의 MLX 0.31.2 바이너리는 macOS 26용입니다.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip check
QT_QPA_PLATFORM=offscreen .venv/bin/python -B -m unittest discover -s tests -v
```

테스트는 모델·오디오 장치·네트워크 없이 실행합니다. `requirements.txt`는 개발 시
허용 범위, `requirements-lock.txt`는 Preview에서 검증한 전체 빌드 환경입니다.
의존성 갱신은 깨끗한 환경에서 테스트와 실제 앱 추론을 다시 검증한 뒤 반영합니다.

## UI와 아이콘

```sh
QT_QPA_PLATFORM=cocoa .venv/bin/python -B -m unittest discover -s tests -p preview_ui.py -v
.venv/bin/python -B scripts/build_icon.py
```

UI 미리보기는 합성 예문만 사용하며 `output/ui/`에 저장됩니다. 공개 문서에 사용할
화면은 내용을 확인한 뒤 `docs/images/`에 복사합니다. 아이콘 원본은
`assets/zoomtrans.svg`입니다. 개발용 실행기 템플릿은 `scripts/launcher.sh`이며
다운로드 앱에는 사용하지 않습니다.

## 독립 앱 빌드

전역 패키지가 섞이지 않은 가상환경에서 실행합니다.

```sh
.venv/bin/python scripts/build_release.py
.venv/bin/python scripts/verify_release.py
```

빌드는 `dist/zoomtrans.app`을 생성합니다. Python, Qt, PortAudio, MLX·Metal,
Whisper 코드·데이터와 컴파일된 Swift 오디오 도구를 포함합니다. PyTorch는
mlx-whisper 설치 의존성이지만 앱 추론에 사용하지 않아 번들에서 제외합니다.
모델·드라이버·Ollama 서버와 실제 대화 기록은 포함하지 않습니다.

`build/notices`의 원본 라이선스와 의존성 목록을 앱에 넣습니다. Qt/PySide6는
동적 라이브러리로 배포하며 [외부 구성요소 고지](../THIRD_PARTY_NOTICES.md)의
라이선스와 소스 주소를 함께 제공합니다. 빌드는 ad-hoc 서명을 수행하고 검증합니다.
Developer ID 서명·공증은 이 Preview 범위에 포함하지 않습니다.

배포에 필요한 Qt 원문 라이선스·외부 구성요소 고지와 mlx-whisper 라이선스는
`packaging/licenses/`에도 보관합니다. 원문 출처와 SHA-256은 `sources.json`에
있으며 갱신 도구는 `scripts/fetch_license_texts.py`입니다.

## 실제 추론 검증과 ZIP

로컬 Ollama와 캐시된 Whisper·기본 번역·12B 모델이 모두 준비된 상태에서 실행합니다.

```sh
.venv/bin/python scripts/verify_release.py --inference --package
```

앱을 임시 폴더로 복사하고 Python·가상환경·Homebrew 경로 없이 실행합니다.
버전·도움말·Qt 화면·오디오 도구의 읽기 전용 장치 조회를 확인합니다. macOS 합성
음성으로 만든 WAV를 실제 MLX로 인식하고 기본 모델과 12B로 번역하며 로그 저장과
단어 타임스탬프·모델 선택을 확인합니다. 오디오 캡처나 출력 장치 전환은 하지 않습니다.

모든 검증이 통과한 경우에만 ZIP과 `SHA256SUMS.txt`를 생성합니다. 검증 결과와
화면은 `output/release-check/`에 저장합니다. 이것은 별도 Mac에서의 검증을
대체하지 않으며, 기본 지원 환경 외의 호환성을 보장하지 않습니다.

오디오 장치 전환·복원은 테스트의 오류 경로 검증에 더해 실제 세션에서도 확인해야
합니다. 진행 중인 회의를 중단하거나 출력 장치를 바꿔 릴리스를 검증하지 마세요.

## GitHub Actions와 게시

CI는 macOS 26 ARM64에서 단위·Qt 테스트를 실행합니다. 수동 실행 또는 버전 태그는
독립 앱을 빌드하고 가벼운 실행 검증 후 Actions artifact로 보관합니다. CI에서 모델을
다운로드하거나 미검증 빌드를 자동 공개하지 않습니다.

실제 추론 검증까지 통과한 동일 빌드를 다음과 같이 게시합니다.

```sh
git tag v0.1.0-preview.1
git push origin v0.1.0-preview.1
gh release create v0.1.0-preview.1 \
  dist/zoomtrans-0.1.0-preview.1-macos26-arm64.zip dist/SHA256SUMS.txt \
  --prerelease --title "ZoomTrans 0.1.0 Preview 1" \
  --notes-file docs/release-notes.md
```

GitHub 인증은 로컬 자격 증명 또는 Actions의 제한된 토큰으로만 처리합니다.
로그·모델·가상환경·로컬 설정과 비밀정보를 커밋하지 마세요. 새 버전은
`app_paths.VERSION`, 앱 번들 버전, 변경 이력과 릴리스 문서를 함께 갱신합니다.
