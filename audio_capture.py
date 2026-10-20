import hashlib
import os
import queue
import subprocess
import threading
import numpy as np
import sounddevice as sd
from messages import SubtitleEvent, RuntimeStatus
from messages import AudioChunk
from time import monotonic


SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_SECONDS)


def list_devices():
    devices = sd.query_devices()
    print("\n사용 가능한 오디오 입력 장치:")
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            print(f"  [{i}] {dev['name']}")
    print()


def find_device_index(name: str) -> int | None:
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0 and name.lower() in dev["name"].lower():
            return i
    return None


class AudioCapture:
    def __init__(self, device: str | int | None, audio_queue: queue.Queue, status_sink=None):
        self.audio_queue = audio_queue
        self.device = device
        self._status_sink = status_sink
        self._stream = None
        self._stop_event = threading.Event()

    def _callback(self, indata, frames, time, status):
        if status:
            print(f"[오디오] {status}")
        audio_chunk = indata[:, 0].copy().astype(np.float32)
        if self._status_sink is not None:
            level = float(np.sqrt(np.mean(audio_chunk ** 2)))
            self._status_sink.put_nowait(RuntimeStatus("input", "level", level=level))
        captured = AudioChunk(audio_chunk, monotonic())
        try:
            self.audio_queue.put_nowait(captured)
        except queue.Full:
            # STT가 따라가지 못하면 가장 오래된 청크 버리고 새 것 추가
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.put_nowait(captured)
            except queue.Empty:
                pass

    def start(self):
        device_index = None
        if isinstance(self.device, str):
            device_index = find_device_index(self.device)
            if device_index is None:
                raise ValueError(f"장치를 찾을 수 없습니다: '{self.device}'\n--list-devices 로 장치 목록을 확인하세요.")
        elif isinstance(self.device, int):
            device_index = self.device

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=device_index,
            blocksize=CHUNK_SAMPLES,
            callback=self._callback,
        )
        self._stream.start()
        if self._status_sink is not None:
            self._status_sink.put_nowait(RuntimeStatus("input", "ready"))
        print(f"[오디오] 캡처 시작 ({self._stream.device})")

    def stop(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        print("[오디오] 캡처 중지")


class SystemAudioCapture:
    """'다중 출력 기기'(BlackHole + 스피커/에어팟)로 자동 전환 후 BlackHole에서 캡처.
    종료 시 원래 출력 장치로 복원. 별도 권한 불필요."""

    SWITCH_BIN     = os.path.join(os.path.dirname(__file__), "audio_switch")
    SWITCH_SRC     = os.path.join(os.path.dirname(__file__), "audio_switch.swift")
    SWITCH_HASH    = os.path.join(os.path.dirname(__file__), ".audio_switch_hash")
    BLACKHOLE_NAME = "BlackHole 16ch"

    STATUS_COOLDOWN = 60.0  # 초 — 반복되는 재전환 경고를 배너로 스팸하지 않도록

    def __init__(self, audio_queue: queue.Queue, silent: bool = False, status_sink=None):
        self.audio_queue      = audio_queue
        self._silent          = silent
        self._status_sink     = status_sink
        self._original_output = None
        self._multi_out_id    = None
        self._created_device  = False
        self._capture         = None
        self._stop_event      = threading.Event()
        self._monitor_thread  = None

    # ── 바이너리 관리 ──────────────────────────────────────────────────────

    def _src_hash(self) -> str:
        h = hashlib.sha256()
        with open(self.SWITCH_SRC, "rb") as f:
            h.update(f.read())
        return h.hexdigest()

    def _binary_current(self) -> bool:
        if not os.path.exists(self.SWITCH_BIN) or not os.path.exists(self.SWITCH_HASH):
            return False
        with open(self.SWITCH_HASH) as f:
            return f.read().strip() == self._src_hash()

    def _ensure_switch_binary(self):
        from app_paths import bundled
        if bundled():
            if not os.path.isfile(self.SWITCH_BIN):
                raise RuntimeError("앱의 오디오 도구가 없습니다. 앱을 다시 설치해 주세요.")
            return
        if self._binary_current():
            return
        print("[시스템오디오] audio_switch 컴파일 중... (최초 1회)")
        result = subprocess.run([
            "/usr/bin/swiftc", self.SWITCH_SRC,
            "-framework", "CoreAudio",
            "-framework", "Foundation",
            "-O", "-o", self.SWITCH_BIN,
        ], capture_output=True, text=True)
        if result.returncode != 0:
            if os.path.exists(self.SWITCH_BIN):
                os.unlink(self.SWITCH_BIN)
            raise RuntimeError(f"[시스템오디오] 컴파일 실패:\n{result.stderr}")
        with open(self.SWITCH_HASH, "w") as f:
            f.write(self._src_hash())
        print("[시스템오디오] 컴파일 완료")

    SWITCH_TIMEOUT = 5  # 초 — CoreAudio 호출이 멈춰도 앱 전체가 행(hang)되지 않도록

    def _run_switch(self, *args) -> str:
        try:
            result = subprocess.run([self.SWITCH_BIN, *args],
                                    capture_output=True, text=True,
                                    timeout=self.SWITCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"audio_switch {' '.join(args)} 시간 초과 ({self.SWITCH_TIMEOUT}초)")
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return result.stdout.strip()

    # ── 상태 알림 (오버레이 배너용, 터미널 없이 실행돼도 사용자가 보게) ──────────

    def _emit_status(self, text: str):
        if self._status_sink is None:
            return
        try:
            self._status_sink.put_nowait(SubtitleEvent("status", text=text))
        except Exception:
            pass

    # ── 출력 장치 탐색 ─────────────────────────────────────────────────────

    def _find_device_id_by_name(self, name: str) -> int | None:
        lines = self._run_switch("list").splitlines()
        matches = []
        for line in lines:
            parts = line.split("\t", 1)
            if len(parts) == 2 and name.lower() in parts[1].lower():
                matches.append((int(parts[0]), parts[1]))
        if len(matches) > 1:
            warning = (f"'{name}' 이름이 겹치는 장치가 {len(matches)}개 발견됨 "
                       f"({', '.join(f'{i}:{n}' for i, n in matches)}) — 첫 번째({matches[0][0]})를 사용합니다. "
                       "무음이면 중복 장치를 정리하세요 (예: Bluetooth 재연결).")
            print(f"[시스템오디오] 경고: {warning}")
            self._emit_status(f"⚠️ {warning}")
        return matches[0][0] if matches else None

    def _find_ams2_multi_output(self) -> int | None:
        """Audio MIDI Setup으로 만든 다중 출력 기기 ID 반환 (ZoomTrans 제외)"""
        lines = self._run_switch("list").splitlines()
        matches = []
        for line in lines:
            parts = line.split("\t", 1)
            if len(parts) == 2:
                dev_name = parts[1]
                if ("다중 출력" in dev_name or "multi-output" in dev_name.lower()) \
                        and "zoomtrans" not in dev_name.lower():
                    device_id = int(parts[0])
                    if self._includes_blackhole(device_id):
                        matches.append((device_id, dev_name))
        if len(matches) > 1:
            warning = (f"다중 출력 기기가 {len(matches)}개 발견됨 "
                       f"({', '.join(f'{i}:{n}' for i, n in matches)}) — 첫 번째({matches[0][0]})를 사용합니다.")
            print(f"[시스템오디오] 경고: {warning}")
            self._emit_status(f"⚠️ {warning}")
        return matches[0][0] if matches else None

    def _includes_blackhole(self, device_id):
        try:
            info = self._run_switch("info", str(device_id))
        except RuntimeError as e:
            self._emit_status(f"출력 장치 구성을 확인할 수 없습니다: {e}")
            return False
        # info lists subdevices as ID TAB name. Do not trust the aggregate name.
        return any(
            len(parts := line.strip().split("\t", 1)) == 2
            and parts[1].casefold() == self.BLACKHOLE_NAME.casefold()
            for line in info.splitlines()
        )

    def _verify_output(self):
        if int(self._run_switch("get")) != self._multi_out_id:
            raise RuntimeError("시스템 출력 장치 전환이 적용되지 않았습니다.")

    # ── 공개 인터페이스 ────────────────────────────────────────────────────

    def start(self):
        try:
            self._start()
        except Exception:
            try:
                self.stop()
            except Exception as e:
                print(f"[시스템오디오] 시작 실패 후 정리 오류: {e}")
            raise

    def _start(self):
        import time
        self._ensure_switch_binary()

        self._original_output = int(self._run_switch("get"))

        if self._silent:
            # 무음 모드: BlackHole만 출력으로 전환 (스피커 없음)
            blackhole_id = self._find_device_id_by_name(self.BLACKHOLE_NAME)
            if blackhole_id is None:
                raise RuntimeError(
                    "[시스템오디오] BlackHole 16ch를 찾을 수 없습니다.\n"
                    "https://github.com/ExistentialAudio/BlackHole 에서 설치 후 재실행하세요."
                )
            self._run_switch("set", str(blackhole_id))
            time.sleep(0.5)
            self._multi_out_id = blackhole_id
            self._created_device = False
            print(f"[시스템오디오] 무음 모드: 출력 장치 → BlackHole 16ch (스피커 없음)")
        else:
            # 1) Audio MIDI Setup으로 미리 만든 다중 출력 기기 우선 사용
            #    (macOS 26에서 프로그래밍 생성 장치는 기본 출력으로 설정 불가)
            ams2_id = self._find_ams2_multi_output()
            if ams2_id is not None:
                if ams2_id == self._original_output:
                    self._multi_out_id = ams2_id
                    self._created_device = False
                    print(f"[시스템오디오] 기존 다중 출력 기기 사용 중 (ID {ams2_id})")
                else:
                    self._run_switch("set", str(ams2_id))
                    time.sleep(0.5)
                    if int(self._run_switch("get")) == ams2_id:
                        self._multi_out_id = ams2_id
                        self._created_device = False
                        print(f"[시스템오디오] 기존 다중 출력 기기로 전환 (ID {ams2_id})")

            # 2) 기존 기기가 없거나 설정 실패 시 BlackHole 직접 생성 시도
            if self._multi_out_id is None:
                blackhole_id = self._find_device_id_by_name(self.BLACKHOLE_NAME)
                if blackhole_id is None:
                    raise RuntimeError(
                        "[시스템오디오] BlackHole 16ch를 찾을 수 없습니다.\n"
                        "https://github.com/ExistentialAudio/BlackHole 에서 설치 후 재실행하세요."
                    )

                if self._original_output == blackhole_id:
                    self._multi_out_id = blackhole_id
                    self._created_device = False
                else:
                    new_id_str = self._run_switch(
                        "create-multi", str(self._original_output), str(blackhole_id)
                    )
                    self._multi_out_id = int(new_id_str)
                    self._created_device = True
                    print(f"[시스템오디오] 다중 출력 기기 생성 (ID {self._multi_out_id})")

                self._run_switch("set", str(self._multi_out_id))
                time.sleep(0.5)

            print(f"[시스템오디오] 출력 장치 → 다중 출력 기기 (BlackHole 포함)")

        self._verify_output()
        self._capture = AudioCapture(device=self.BLACKHOLE_NAME,
                                     audio_queue=self.audio_queue, status_sink=self._status_sink)
        self._capture.start()

        self._monitor_thread = threading.Thread(
            target=self._monitor_output, daemon=True
        )
        self._monitor_thread.start()
        print("[시스템오디오] 시스템 오디오 캡처 시작 (에어팟·스피커 모두 지원)")

    def _monitor_output(self):
        import time
        # 연속 2회(약 6초) 동안 계속 다를 때만 되돌린다 — Audio MIDI Setup에서
        # 잠깐 설정을 확인/조정하는 정도로는 되돌리지 않기 위한 유예.
        mismatch_count   = 0
        last_status_time = 0.0
        while not self._stop_event.wait(3):
            try:
                current = int(self._run_switch("get"))
            except Exception:
                continue

            if self._stop_event.is_set():
                break
            if current == self._multi_out_id:
                mismatch_count = 0
                continue

            mismatch_count += 1
            if mismatch_count < 2:
                continue

            print("[시스템오디오] 출력 장치 변경 감지 → 다중 출력 기기로 재전환")
            # 반복되는 재전환마다 배너를 띄우면 스팸이 되므로 쿨다운 적용
            # (블루투스 불안정 등으로 짧은 시간에 여러 번 발생할 수 있음)
            now = time.monotonic()
            if now - last_status_time >= self.STATUS_COOLDOWN:
                self._emit_status("⚠️ 출력 장치가 불안정합니다 (자동으로 재전환 중) — "
                                   "AirPods 등 블루투스 연결 상태를 확인하세요.")
                last_status_time = now
            try:
                self._run_switch("set", str(self._multi_out_id))
            except Exception:
                pass
            mismatch_count = 0

    def _restore_output(self):
        if self._original_output is not None:
            try:
                self._run_switch("set", str(self._original_output))
                if int(self._run_switch("get")) != self._original_output:
                    raise RuntimeError("원래 출력 장치로의 전환이 적용되지 않았습니다.")
                print("[시스템오디오] 출력 장치 복원 완료")
            except Exception as e:
                print(f"[시스템오디오] 출력 장치 복원 실패: {e}")
                # Retain the original ID for a retry. Do not delete a temporary
                # device that may still be the active system output.
                raise RuntimeError(f"출력 장치를 복원할 수 없습니다: {e}") from e
            self._original_output = None

        if self._created_device and self._multi_out_id is not None:
            try:
                self._run_switch("destroy", str(self._multi_out_id))
                print(f"[시스템오디오] 임시 다중 출력 기기 삭제 완료")
            except Exception as e:
                print(f"[시스템오디오] 임시 기기 삭제 실패: {e}")
                raise RuntimeError(f"임시 출력 장치를 삭제할 수 없습니다: {e}") from e
            self._multi_out_id = None
            self._created_device = False

    def stop(self):
        self._stop_event.set()
        try:
            if self._capture:
                capture, self._capture = self._capture, None
                capture.stop()
        finally:
            if self._monitor_thread:
                # Do not restore while the monitor could still switch it back.
                self._monitor_thread.join()
            self._restore_output()
        print("[시스템오디오] 캡처 중지")
