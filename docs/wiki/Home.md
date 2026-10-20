# ZoomTrans 사용자 가이드

Mac에서 재생되는 회의·강의 소리를 영어 원문과 한국어 자막으로 보여주는 로컬 번역 앱입니다. 회의록 저장과 종료 후 요약도 제공합니다. Zoom 외의 앱에서 재생하는 소리도 사용할 수 있으며, Zoom과 제휴한 제품은 아닙니다.

![ZoomTrans 자막 화면](https://raw.githubusercontent.com/Taeksu0710/zoomtrans/main/docs/images/compact-light.png)

**현재 공개 버전: 0.1.0-preview.1 · Apple Silicon · macOS 26 이상**

[앱 다운로드](https://github.com/Taeksu0710/zoomtrans/releases) · [소스 코드](https://github.com/Taeksu0710/zoomtrans) · [버그 제보](https://github.com/Taeksu0710/zoomtrans/issues)

## 처음 사용한다면

1. 릴리스 ZIP을 풀고 `zoomtrans.app`을 응용 프로그램 폴더에 옮깁니다. Python은 따로 설치하지 않아도 됩니다.
2. **BlackHole 16ch**와 **Ollama**를 설치하고 `ollama pull gemma4:e4b`로 기본 번역 모델을 받습니다.
3. 앱을 열고 시작 준비 창에서 필요한 Whisper 모델을 다운로드합니다.
4. 회의 소리를 스피커로 들을지, 무음으로 자막만 볼지 선택합니다.

자세한 절차와 macOS 첫 실행 방법은 [설치 및 첫 실행](https://github.com/Taeksu0710/zoomtrans/wiki/Installation)을 확인하세요. 현재 Preview에는 Developer ID 서명과 Apple 공증이 없습니다.

## 필요한 안내 찾기

| 궁금한 내용 | 안내 |
| --- | --- |
| 앱 다운로드, BlackHole·Ollama·Whisper 준비 | [설치 및 첫 실행](https://github.com/Taeksu0710/zoomtrans/wiki/Installation) |
| 자막 화면, 기록, 단축키, 회의록·요약 | [사용법](https://github.com/Taeksu0710/zoomtrans/wiki/Usage) |
| 기본 모델, 12B 선택, 문장 연결과 지연 | [모델과 번역 설정](https://github.com/Taeksu0710/zoomtrans/wiki/Models-and-Translation) |
| 실행 차단, 입력 없음, 모델 오류, 소리 복원 | [문제 해결](https://github.com/Taeksu0710/zoomtrans/wiki/Troubleshooting) |
| 소스 실행, 테스트, 독립 앱 빌드와 릴리스 | [개발 및 배포](https://github.com/Taeksu0710/zoomtrans/wiki/Development) |

## 알아두면 좋은 점

- BlackHole·Ollama 서버·AI 모델은 앱에 포함되지 않습니다. 초기 설치와 다운로드에는 인터넷이 필요합니다.
- 준비 이후 기본 구성의 음성인식·번역·요약은 로컬에서 처리합니다. 회의 오디오는 파일로 녹음하지 않습니다.
- 회의록과 요약에는 실제 대화가 들어갑니다. 기본 저장 위치는 `~/Library/Application Support/ZoomTrans/logs`입니다.
- **12B 옵션은 기본 꺼짐**입니다. 문맥이 복잡한 문장에 사용할 수 있지만 계산량과 지연이 늘어날 수 있습니다.
- 번역·요약에는 오류나 누락이 있을 수 있습니다. 중요한 숫자·날짜·조건은 원문과 확인하세요.

프로젝트 코드는 [MIT 라이선스](https://github.com/Taeksu0710/zoomtrans/blob/main/LICENSE)입니다. 외부 라이브러리와 모델의 조건은 [외부 구성요소 고지](https://github.com/Taeksu0710/zoomtrans/blob/main/THIRD_PARTY_NOTICES.md)를 참고하세요.
