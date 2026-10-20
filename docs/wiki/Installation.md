# 설치 및 첫 실행

[홈](https://github.com/Taeksu0710/zoomtrans/wiki) · [사용법](https://github.com/Taeksu0710/zoomtrans/wiki/Usage) · [문제 해결](https://github.com/Taeksu0710/zoomtrans/wiki/Troubleshooting)

## 지원 환경

- Apple Silicon Mac, macOS 26 이상. Intel과 Rosetta 실행은 지원하지 않습니다.
- 기본 모델과 Whisper를 함께 실행할 메모리 및 모델 다운로드용 디스크 공간이 필요합니다. 실제 사용량은 모델·음성 길이·동시 실행 앱에 따라 달라집니다.
- 12B는 선택 기능입니다. 메모리가 부족하거나 지연이 크면 기본 모델을 사용하세요.

## 다운로드 앱

[Releases](https://github.com/Taeksu0710/zoomtrans/releases)에서
`zoomtrans-버전-macos26-arm64.zip`을 다운로드하고 압축을 푼 뒤
`zoomtrans.app`을 `/Applications`로 옮깁니다. Python과 Xcode는 필요하지 않습니다.

체크섬은 릴리스의 `SHA256SUMS.txt`와 다음 명령의 결과를 비교합니다.

```sh
shasum -a 256 zoomtrans-0.1.0-preview.1-macos26-arm64.zip
```

### macOS 첫 실행

이 Preview는 Developer ID 서명과 Apple 공증이 없습니다. Apple Silicon 실행에
필요한 ad-hoc 서명만 포함됩니다. 개발자를 확인할 수 없다는 경고가 표시되면
출처를 확인한 후 **시스템 설정 → 개인정보 보호 및 보안 → 그래도 열기**로
해당 앱만 허용합니다. [Apple 공식 안내](https://support.apple.com/en-us/102445)를
따르세요. 시스템 전체의 보안 기능을 끌 필요는 없습니다.

## BlackHole 16ch

[BlackHole 공식 저장소](https://github.com/ExistentialAudio/BlackHole)의 설치 방법을
따라 **16ch 버전**을 설치합니다. Homebrew 사용자는 다음 명령을 사용할 수 있습니다.

```sh
brew install --cask blackhole-16ch
```

설치 후 장치가 나타나지 않으면 Mac을 재시작하세요. 앱은 시작할 때 시스템 출력을
BlackHole을 포함하는 출력 장치로 전환하고 종료할 때 원래 출력으로 복원합니다.
강제 종료나 장치 연결 해제 후 소리가 나오지 않으면 macOS 소리 설정에서 원래
스피커·헤드폰을 다시 선택하세요. 생성한 임시 다중 출력 장치는 정상 종료 시 삭제합니다.

오디오 입력 권한을 요청하면 허용합니다. 입력이 없으면 개인정보 보호 및 보안의
마이크 권한과 BlackHole 장치 상태를 확인하세요. 화면 녹화 권한은 요구하지 않습니다.

## Ollama와 번역 모델

[Ollama 공식 사이트](https://ollama.com/download/mac)에서 앱을 설치하고 실행합니다.
터미널에서 기본 모델을 다운로드합니다.

```sh
ollama pull gemma4:e4b
ollama list
```

12B 옵션을 사용할 때만 다음 모델을 추가합니다.

```sh
ollama pull gemma4:12b
```

기본 연결은 로컬 Ollama 서버입니다. 시작 준비 창에 연결 오류가 나오면 Ollama가
실행 중인지 확인하고 **다시 확인**을 선택하세요. 서버·모델을 자동 설치하거나
원격 서버로 우회하지 않습니다. Ollama 설치 버전이 모델을 지원해야 합니다.

## Whisper 모델

앱 시작 준비 창에서 **Whisper 모델 다운로드**를 선택합니다. 기본 모델은
`mlx-community/whisper-large-v3-mlx`입니다. Hugging Face의 사용자 모델 캐시에
저장되며 앱 업데이트 때 다시 받을 필요가 없습니다. 기본 캐시는
`~/.cache/huggingface/hub`이고 Hugging Face의 환경 변수 설정이 있으면 그 설정을 따릅니다.

다운로드 중에는 창을 유지하세요. 실패하면 인터넷과 디스크 공간을 확인한 뒤 다시
시도합니다. 이 버튼은 선택한 Whisper 모델만 다운로드하며 번역 모델은 받지 않습니다.
작은 Whisper 모델을 선택하려면 소스 실행이나 아래 CLI를 사용하세요.

```sh
/Applications/zoomtrans.app/Contents/MacOS/zoomtrans --whisper-model small
```

준비 완료 후 앱은 Whisper를 오프라인 캐시에서 읽습니다. 기본 모델을 바꾸면 해당
모델의 준비가 별도로 필요합니다. 앱에는 AI 모델 가중치를 포함하지 않습니다.

## 저장 위치와 개인정보

다운로드 앱: `~/Library/Application Support/ZoomTrans/logs`.
소스 실행: 프로젝트의 `logs/`. `--log-dir`로 변경할 수 있습니다.

원문·번역·선택한 요약이 로컬 파일에 저장됩니다. 회의 오디오는 파일로 녹음하지 않습니다.
회의록 옆의 진단 JSONL에는 처리 시간과 모델 등의 정보만 기록합니다. 앱은 회의 내용을
외부 분석 서비스로 보내지 않습니다. 초기 모델 다운로드와 사용자가 여는 설치 안내는
인터넷에 연결합니다. 기기 권한과 대화 기록 취급은 사용 환경의 규칙을 따르세요.

## 다음 단계

[사용법](https://github.com/Taeksu0710/zoomtrans/wiki/Usage)에서 자막 화면과 회의록 저장 방법을 확인하세요. 입력·다운로드·소리 복원 문제는 [문제 해결](https://github.com/Taeksu0710/zoomtrans/wiki/Troubleshooting)에 정리했습니다.
