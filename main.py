#!/usr/bin/env python3
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import argparse
import queue
import sys
from datetime import datetime
from app_paths import VERSION, default_log_dir

from audio_capture import AudioCapture, SystemAudioCapture, list_devices, CHUNK_SECONDS
from logger import TranscriptLogger
from messages import Broadcaster
from session import MeetingSession, stop_pipeline
from streaming_transcriber import StreamingTranscriber
from transcriber import Transcriber
from performance import PerformanceLogger
from translator import Translator

DEFAULT_DEVICE = "system"
DEFAULT_WHISPER_MODEL = "large-v3"
DEFAULT_OLLAMA_MODEL = "gemma4:e4b"
# Optional session model; normal launches leave selective routing disabled.
DEFAULT_COMPLEX_MODEL = "gemma4:12b"
DEFAULT_LOG_DIR = default_log_dir()


def _has_transcript_content(filepath):
    try:
        with open(filepath, encoding="utf-8") as file:
            text = file.read()
            return "[KO]" in text or "[EN]" in text
    except OSError:
        return False


def parse_args():
    parser = argparse.ArgumentParser(
        description="Zoom 영어 회의 → 실시간 한국어 자막",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py --list-devices
  python main.py                              # 시작 시 모드 선택 다이얼로그
  python main.py --silent                     # 무음 모드 (다이얼로그 생략)
  python main.py --device "BlackHole 16ch"   # BlackHole 직접 지정
  python main.py --device "BlackHole 16ch" --whisper-model medium

속도 우선 설정 (이미 캐시돼 있어 바로 실행 가능):
  python main.py --whisper-model small        # large-v3 대비 훨씬 빠름, 정확도는 약간 낮음
  python main.py --stt-mode streaming         # 발화 중 영어 자막을 조기에 표시 (실험적)

  (medium/base/tiny는 오프라인 환경(HF_HUB_OFFLINE=1)이라 최초 다운로드가 필요합니다.)
        """,
    )
    parser.add_argument("--list-devices", action="store_true", help="오디오 입력 장치 목록 출력")
    parser.add_argument("--version", action="version", version=f"ZoomTrans {VERSION}")
    parser.add_argument("--self-test", metavar="WAV", help="배포 검증: 합성 WAV를 인식·번역하고 종료 (오디오 장치 변경 없음)")
    parser.add_argument("--ui-smoke-test", metavar="PNG", help="배포 검증: 오디오 캡처 없이 자막 창을 렌더링하고 종료")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"오디오 입력 장치 이름 (기본: {DEFAULT_DEVICE})")
    parser.add_argument("--silent", action="store_true", help="무음 모드: 시스템 출력을 BlackHole로 전환 (종료 시 자동 복원)")
    parser.add_argument(
        "--whisper-model",
        default=DEFAULT_WHISPER_MODEL,
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help=f"Whisper 모델 크기 (기본: {DEFAULT_WHISPER_MODEL})",
    )
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL, help=f"Ollama 모델 (기본: {DEFAULT_OLLAMA_MODEL})")
    parser.add_argument("--complex-model", default=None,
                        help=f"긴 문장용 로컬 모델을 이번 실행에 활성화 (예: {DEFAULT_COMPLEX_MODEL}); 기본 꺼짐, none이면 사용 안 함")
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help=f"회의록 저장 폴더 (기본: {DEFAULT_LOG_DIR})")
    parser.add_argument(
        "--stt-mode",
        default="batch",
        choices=["batch", "streaming"],
        help="음성인식 모드: batch=기존 안정 모드, streaming=영어 미리보기/문장 확정 후 번역 (실험적, 기본: batch)",
    )
    return parser.parse_args()


def create_session(args, silent):
    os.makedirs(args.log_dir, exist_ok=True)
    filename = os.path.join(args.log_dir, f"meeting_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    audio_queue = queue.Queue(maxsize=round(50 / CHUNK_SECONDS))
    performance = PerformanceLogger(filename.replace(".txt", "_performance.jsonl"))
    text_queue, subtitle_queue, log_queue, error_queue = [queue.Queue() for _ in range(4)]
    broadcast = Broadcaster(subtitle_queue, log_queue)
    if args.device.lower() == "system":
        capture = SystemAudioCapture(audio_queue, silent=silent, status_sink=broadcast)
    else:
        capture = AudioCapture(args.device, audio_queue, status_sink=broadcast)
    cls = StreamingTranscriber if args.stt_mode == "streaming" else Transcriber
    transcriber = cls(args.whisper_model, audio_queue, text_queue, broadcast, error_queue,
                      performance=performance)
    requested_model = (getattr(args, "complex_model", None) or "").strip()
    complex_enabled = bool(requested_model and requested_model.lower() != "none")
    translator = Translator(args.model, text_queue, broadcast, performance=performance,
                            complex_model=requested_model if complex_enabled else DEFAULT_COMPLEX_MODEL,
                            complex_enabled=complex_enabled)
    logger = TranscriptLogger(log_queue, filename, error_queue, status_sink=broadcast)
    print(f"[회의] STT={args.stt_mode} · 저장={filename}")
    return MeetingSession(capture, transcriber, translator, logger, subtitle_queue,
                          error_queue, filename, args.model, performance=performance)


def main():
    args = parse_args()
    if args.list_devices:
        list_devices()
        return 0
    if args.self_test:
        from release_check import run_inference_check
        return run_inference_check(args)
    if args.ui_smoke_test:
        from release_check import run_ui_check
        return run_ui_check(args.ui_smoke_test)
    # Device listing and CLI help must work without Qt or model initialization.
    from ui_app import run_desktop
    return run_desktop(args, create_session, _has_transcript_content)


if __name__ == "__main__":
    sys.exit(main())
