# 문제 해결

[홈](https://github.com/Taeksu0710/zoomtrans/wiki) · [설치](https://github.com/Taeksu0710/zoomtrans/wiki/Installation) · [모델과 번역 설정](https://github.com/Taeksu0710/zoomtrans/wiki/Models-and-Translation)

## 앱이 실행되지 않음

1. Apple Silicon Mac인지, macOS 26 이상인지 확인합니다. Intel과 Rosetta 실행은 지원하지 않습니다.
2. [Releases](https://github.com/Taeksu0710/zoomtrans/releases)에서 받은 ZIP을 완전히 풀고 앱을 응용 프로그램 폴더로 옮깁니다.
3. 현재 Preview는 Developer ID 서명·공증이 없습니다. 출처를 확인한 뒤 **시스템 설정 → 개인정보 보호 및 보안 → 그래도 열기**로 해당 앱을 허용합니다. [Apple 공식 안내](https://support.apple.com/en-us/102445)를 따르세요.
4. 파일이 손상됐다는 메시지가 계속되면 다시 다운로드하고 릴리스의 SHA-256 체크섬을 비교합니다.

시스템 전체 보안 설정을 해제할 필요는 없습니다.

## 시작 준비 창에서 진행되지 않음

| 표시 내용 | 확인할 사항 |
| --- | --- |
| BlackHole 없음 | **16ch** 버전을 설치했는지 확인. 설치 후 필요하면 Mac 재시작 |
| Ollama 연결 불가 | Ollama 앱을 설치하고 실행한 뒤 **다시 확인** |
| 번역 모델 없음 | 터미널에서 `ollama pull gemma4:e4b` 실행 |
| Whisper 없음 | **Whisper 모델 다운로드** 선택. 인터넷·디스크 공간 확인 |
| 모델 다운로드 실패 | 연결과 여유 공간을 확인한 뒤 다시 시도. 완료까지 창 유지 |

Whisper, Ollama 번역 모델, BlackHole은 서로 다른 구성요소입니다. 한 항목을 설치해도 다른 항목이 자동 설치되지는 않습니다.

## 회의 소리가 인식되지 않음

- 회의나 강의에서 실제 소리가 재생 중인지 확인합니다.
- 오디오 입력 권한을 요청하면 허용하고, macOS 개인정보 보호 및 보안의 마이크 권한을 확인합니다.
- BlackHole 16ch가 입력 장치로 보이는지 확인합니다.
- 장치 연결을 바꾼 뒤 문제가 생겼다면 앱을 정상 종료하고 원래 출력 장치를 선택한 다음 다시 실행합니다.
- 소스나 터미널에서 실행할 수 있다면 `--list-devices`로 입력 장치를 확인합니다.

```sh
/Applications/zoomtrans.app/Contents/MacOS/zoomtrans --list-devices
```

## 앱 사용 후 Mac에서 소리가 안 남

무음 모드에서는 시스템 출력이 BlackHole로 전환됩니다. 정상 종료 시 복원하지만 강제 종료나 장치 연결 해제로 복원하지 못할 수 있습니다.

**시스템 설정 → 사운드 → 출력**에서 원래 스피커나 헤드폰을 직접 선택하세요. 임시 다중 출력 장치가 남았는지는 오디오 MIDI 설정에서 확인할 수 있습니다.

## 영어만 나오거나 한국어가 늦게 나옴

- Ollama가 계속 실행 중인지, `ollama list`에 기본 모델이 있는지 확인합니다.
- 상태 표시가 다음 구절 대기라면 미완성 발화를 기다리는 중일 수 있습니다.
- 번역 대기 문장 수가 늘어난다면 앞선 작업의 계산 시간이 길어진 상태입니다.
- 12B를 켰다면 끄고 비교합니다. 처음 사용하는 모델은 준비 시간이 추가될 수 있습니다.
- 더 작은 Whisper 모델을 사용할 수 있지만 음성인식 정확도가 달라질 수 있습니다.

## 12B를 켰는데 기본 모델로 번역됨

40단어 미만이고 조건·시간 표현 조합도 없는 문장은 기본 모델을 사용합니다. 적용 대상이어도 12B가 없거나 실패하면 알림을 표시하고 기본 모델로 대체합니다. `ollama pull gemma4:12b`로 모델을 준비하세요. 옵션은 다음 일반 실행에서 다시 꺼집니다.

## 회의록·요약을 찾을 수 없음

다운로드 앱은 `~/Library/Application Support/ZoomTrans/logs`, 소스 실행은 실행 위치의 `logs/`를 확인합니다. `--log-dir`를 지정했다면 그 위치에 저장합니다. 요약은 종료 후 생성하기를 선택한 경우에만 만듭니다.

## 버그 제보

[Issues](https://github.com/Taeksu0710/zoomtrans/issues)에 앱·macOS·Ollama 버전, Mac 칩·메모리, 사용 모델, 재현 순서와 기대·실제 동작을 남겨 주세요. 합성 예문을 사용하고 실제 회의록·녹음·비밀번호·인증 토큰은 첨부하지 마세요.
