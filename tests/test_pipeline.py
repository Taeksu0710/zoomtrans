"""Regression tests; no model loading, Ollama server or audio device required.

Run: .venv/bin/python -B -m unittest discover -s tests -v
"""

import queue
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import httpx

# MLX imports are lazy; these tests never initialize a GPU.
import main
import streaming_transcriber
import transcriber

from audio_capture import AudioCapture, SystemAudioCapture
from logger import TranscriptLogger
from messages import Broadcaster, SubtitleEvent
from subtitle_model import SubtitleStore
from session import MeetingSession
from streaming_transcriber import StreamingTranscriber
from transcriber import Transcriber
from translator import Translator


def drain(q):
    items = []
    while not q.empty():
        item = q.get_nowait()
        if isinstance(item, SubtitleEvent):
            items.append(item)
    return items


def event(utterance_id="a", **kwargs):
    return SubtitleEvent("en_only", utterance_id=utterance_id, revision=1,
                         text="Hello there.", **kwargs)


class PipelineTests(unittest.TestCase):
    def make_bus(self):
        subtitles, logs = queue.Queue(), queue.Queue()
        return Broadcaster(subtitles, logs), subtitles, logs

    def make_transcriber(self, cls=Transcriber, bus=None):
        t = cls("small", queue.Queue(), queue.Queue(), bus or queue.Queue())
        t._warmup = Mock()
        t._calibrate_silence_threshold = Mock(return_value=0.005)
        return t

    def make_translator(self, bus, text_queue):
        with patch("translator.ollama.Client") as client:
            t = Translator("test", text_queue, bus)
        t._warmup = Mock()
        return t, client.return_value

    def test_both_stt_modes_drain_queue_and_flush_last_utterance(self):
        for cls in (Transcriber, StreamingTranscriber):
            with self.subTest(mode=cls.__name__):
                t = self.make_transcriber(cls)
                t.audio_queue.put(np.full(8000, 0.1, dtype=np.float32))
                t.audio_queue.put(np.full(8000, 0.1, dtype=np.float32))
                t._stop_event.set()
                t._transcribe = Mock()
                t._run()
                t._transcribe.assert_called_once()
                self.assertEqual(len(t._transcribe.call_args.args[0]), 16000)
                self.assertTrue(t.audio_queue.empty())

    def test_stop_waits_for_inference_before_returning(self):
        t = self.make_transcriber()
        t.audio_queue.put(np.full(8000, 0.1, dtype=np.float32))
        t.audio_queue.put(np.zeros(24000, dtype=np.float32))
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

        def infer(*args):
            entered.set()
            self.assertTrue(release.wait(3))

        t._transcribe = infer
        t.start()
        self.assertTrue(entered.wait(3))
        stopper = threading.Thread(target=lambda: (t.stop(), stopped.set()))
        stopper.start()
        try:
            self.assertFalse(stopped.wait(0.05))
        finally:
            release.set()
            stopper.join(3)
        self.assertTrue(stopped.is_set())
        self.assertFalse(t._thread.is_alive())

    def test_calibration_preserves_audio_when_stop_arrives(self):
        t = Transcriber("small", queue.Queue(), queue.Queue(), queue.Queue())
        chunk = np.full(8000, 0.1, dtype=np.float32)

        def get(**kwargs):
            t._stop_event.set()
            return chunk

        with patch.object(t.audio_queue, "get", side_effect=get):
            self.assertEqual(t._calibrate_silence_threshold(), 0.005)
        t._warmup = Mock()
        t._transcribe = Mock()
        t._run()
        np.testing.assert_array_equal(t._transcribe.call_args.args[0], chunk)

    def test_model_loading_failure_reaches_error_queue_in_both_modes(self):
        for cls in (Transcriber, StreamingTranscriber):
            with self.subTest(mode=cls.__name__):
                t = self.make_transcriber(cls)
                t._warmup.side_effect = RuntimeError("model missing offline")
                t.start()
                t._thread.join(3)
                self.assertFalse(t._thread.is_alive())
                self.assertIn("model missing offline", t.error_queue.get_nowait())

    def test_final_original_is_published_before_translation_job(self):
        bus, subtitles, logs = self.make_bus()
        t = self.make_transcriber(bus=bus)
        original_put = t.text_queue.put

        def put(item):
            self.assertEqual(subtitles.queue[0], item)
            self.assertEqual(logs.queue[0], item)
            original_put(item)

        t.text_queue.put = put
        t._publish_text("Ready.")

    def test_streaming_final_replaces_revised_prefix_and_rejects_old_translation(self):
        bus, subtitles, logs = self.make_bus()
        t = self.make_transcriber(StreamingTranscriber, bus)
        preview = {"text": "I think we should proceed", "language": "en"}
        revised = {"text": "I believe we should proceed", "language": "en"}
        audio = np.full(16000, 0.1, dtype=np.float32)
        with patch.object(streaming_transcriber.mlx_whisper, "transcribe", create=True,
                          side_effect=[preview, preview, revised]):
            for _ in range(2):
                t._last_poll_time = 0
                t._poll_buffer(audio, 0.005, 0.1)
            self.assertTrue(t.text_queue.empty())
            early = next(e for e in subtitles.queue if isinstance(e, SubtitleEvent))
            self.assertFalse(early.final)
            t._transcribe(audio, 0.005, 0.1)
        final = t.text_queue.get_nowait()
        self.assertEqual(early.utterance_id, final.utterance_id)
        self.assertGreater(final.revision, early.revision)
        bus.put_nowait(replace(early, kind="en_ko", translation="obsolete"))
        bus.put_nowait(replace(final, kind="en_ko", translation="최종 번역"))
        overlay = SubtitleStore()
        for item in drain(subtitles):
            overlay.apply(item)
        self.assertEqual(len(overlay.pairs), 1)
        self.assertEqual(overlay.pairs[0]["en"], revised["text"])
        self.assertEqual(overlay.pairs[0]["ko"], "최종 번역")
        self.assertEqual([item.kind for item in drain(logs)], ["en_only", "en_ko"])

    def test_failed_final_recognition_discards_preview(self):
        bus, subtitles, logs = self.make_bus()
        t = self.make_transcriber(StreamingTranscriber, bus)
        t._publish_text("An early hypothesis", final=False)
        self.assertTrue(t.text_queue.empty())
        early = next(e for e in subtitles.queue if isinstance(e, SubtitleEvent))
        with patch.object(transcriber.mlx_whisper, "transcribe", create=True,
                          side_effect=RuntimeError("decode failed")):
            t._transcribe(np.ones(8000, dtype=np.float32), 0.005, 0.1)
        bus.put_nowait(replace(early, kind="ko_partial", translation="obsolete"))
        self.assertEqual([item.kind for item in drain(subtitles)],
                         ["en_only", "discard", "status"])
        self.assertTrue(logs.empty())

    def test_logger_drains_backlog_after_stop_and_ignores_previews(self):
        q = queue.Queue()
        original = event()
        q.put(replace(original, final=False, text="PREVIEW"))
        q.put(original)
        q.put(replace(original, kind="en_ko", translation="완료된 번역"))
        q.put(replace(event("b"), kind="native", text="마지막 발화"))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "meeting.txt"
            logger = TranscriptLogger(q, str(path))
            logger._stop_event.set()
            logger.start()
            logger.stop()
            text = path.read_text()
            self.assertNotIn("PREVIEW", text)
            self.assertIn("Hello there.", text)
            self.assertIn("완료된 번역", text)
            self.assertIn("마지막 발화", text)
            self.assertTrue(q.empty())
            self.assertTrue(main._has_transcript_content(str(path)))

    def test_logger_reports_file_open_failure(self):
        with tempfile.TemporaryDirectory() as d:
            logger = TranscriptLogger(queue.Queue(), str(Path(d) / "absent" / "log.txt"))
            logger.start()
            logger.stop()
            self.assertIn("회의록을 저장할 수 없습니다", logger.error_queue.get_nowait())

    def test_repeated_text_and_translation_before_original_are_matched_by_id(self):
        overlay = SubtitleStore()
        first, second = event("first"), event("second")
        for item in (replace(first, kind="en_ko", translation="첫 번째"),
                     second, first, replace(second, kind="en_ko", translation="두 번째")):
            overlay.apply(item)
        self.assertEqual(len(overlay.pairs), 2)
        self.assertEqual([p["ko"] for p in overlay.pairs], ["첫 번째", "두 번째"])

    def test_overlay_ignores_retired_and_superseded_results(self):
        overlay = SubtitleStore()
        first = event()
        overlay.apply(first)
        overlay.apply(replace(first, revision=2, kind="native", text="수정된 발화"))
        self.assertFalse(overlay.apply(replace(first, kind="ko_partial", translation="obsolete")))
        self.assertEqual(overlay.pairs[0]["ko"], "수정된 발화")
        overlay.apply(replace(first, revision=3, kind="discard"))
        self.assertFalse(overlay.apply(replace(first, kind="en_ko", translation="obsolete")))
        self.assertEqual(overlay.pairs, [])

    def test_multiline_text_and_errors_preserve_full_model_content(self):
        store = SubtitleStore()
        first = replace(event("a"), text="First\nline", kind="en_ko", translation="첫\n발화")
        native = replace(event("b"), kind="native", text="다음\n발화")
        last = replace(event("c"), text="Last")
        for item in (first, native, last):
            store.apply(item)
        store.apply(replace(last, kind="ko_partial", translation="마지막\n번역"))
        store.apply(replace(last, kind="en_error", error="번역 실패"))
        self.assertEqual(store.pairs[0]["ko"], "첫\n발화")
        self.assertEqual(store.pairs[1]["ko"], "다음\n발화")
        self.assertEqual(store.pairs[2]["ko"], "마지막\n번역")
        self.assertEqual(store.pairs[2]["error"], "번역 실패")
        self.assertFalse(store.pairs[2]["streaming"])

    def test_translation_shutdown_drains_all_final_jobs(self):
        bus, subtitles, logs = self.make_bus()
        q = queue.Queue()
        for item in (event("a"), event("b")):
            bus.put_nowait(item)
            q.put(item)
        t, client = self.make_translator(bus, q)
        client.chat.side_effect = lambda **kwargs: iter([{"message": {"content": "번역"}}])
        t._stop_event.set()
        t.start()
        t.stop()
        completed = [item for item in drain(logs) if item.kind == "en_ko"]
        self.assertEqual([item.utterance_id for item in completed], ["a", "b"])
        self.assertTrue(q.empty())
        self.assertFalse(t._thread.is_alive())

    def test_timeout_exception_and_empty_translation_finish_ui_state(self):
        for failure in (httpx.ReadTimeout("slow"), RuntimeError("broken"), None):
            with self.subTest(failure=failure):
                bus, subtitles, logs = self.make_bus()
                original = event()
                bus.put_nowait(original)
                q = queue.Queue()
                q.put(original)
                t, client = self.make_translator(bus, q)

                def stream():
                    if failure is not None:
                        yield {"message": {"content": "부분 번역"}}
                        raise failure

                client.chat.return_value = stream()
                t._stop_event.set()
                t._run()
                overlay = SubtitleStore()
                for item in drain(subtitles):
                    overlay.apply(item)
                self.assertIsNone(overlay.pairs[0]["ko"])
                self.assertFalse(overlay.pairs[0]["streaming"])
                self.assertEqual(overlay.pairs[0]["state"], "error")
                self.assertTrue(overlay.pairs[0]["error"])
                self.assertEqual([item.kind for item in drain(logs)], ["en_only"])

    def test_revision_change_closes_inflight_translation(self):
        bus, subtitles, logs = self.make_bus()
        original = event()
        bus.put_nowait(original)
        q = queue.Queue()
        q.put(original)
        t, client = self.make_translator(bus, q)
        closed = []

        def stream():
            try:
                yield {"message": {"content": "부분"}}
                bus.put_nowait(replace(original, revision=2, text="Corrected", final=True))
                yield {"message": {"content": "오래된 번역"}}
            finally:
                closed.append(True)

        client.chat.return_value = stream()
        t._stop_event.set()
        t._run()
        self.assertEqual(closed, [True])
        self.assertFalse(any(item.kind in {"en_ko", "en_error"} for item in drain(subtitles)))

    def test_shutdown_order_and_cleanup_after_failure(self):
        order = []
        workers = []
        for name in ("capture", "stt", "translator", "logger"):
            def stop(name=name):
                order.append(name)
                if name == "capture":
                    raise RuntimeError("capture cleanup failed")
            workers.append((name, types.SimpleNamespace(stop=stop)))
        errors = main.stop_pipeline(workers)
        self.assertEqual(order, ["capture", "stt", "translator", "logger"])
        self.assertEqual(len(errors), 1)

    def test_threaded_stt_translation_logger_pipeline_preserves_last_speech(self):
        for cls in (Transcriber, StreamingTranscriber):
            with self.subTest(mode=cls.__name__), tempfile.TemporaryDirectory() as d:
                bus, subtitles, logs = self.make_bus()
                t = self.make_transcriber(cls, bus)
                for chunk in (np.full(8000, 0.1, dtype=np.float32),
                              np.zeros(24000, dtype=np.float32),
                              np.full(8000, 0.1, dtype=np.float32)):
                    t.audio_queue.put(chunk)
                translator, client = self.make_translator(bus, t.text_queue)
                client.chat.side_effect = lambda **kwargs: iter([{"message": {"content": "최종 번역"}}])
                path = Path(d) / "meeting.txt"
                logger = TranscriptLogger(logs, str(path))
                # Drain mode must start before STT stops, because STT is still
                # publishing final jobs while processing its captured backlog.
                translator.begin_drain()
                results = [
                    {"text": "First complete utterance.", "language": "en"},
                    {"text": "Last utterance without trailing silence.", "language": "en"},
                ]
                with patch.object(transcriber.mlx_whisper, "transcribe", create=True, side_effect=results):
                    logger.start()
                    translator.start()
                    t.start()
                    self.assertEqual(main.stop_pipeline([
                        ("stt", t), ("translator", translator), ("logger", logger),
                    ]), [])
                text = path.read_text()
                self.assertIn("First complete utterance.", text)
                self.assertIn("Last utterance without trailing silence.", text)
                self.assertEqual(text.count("[EN]"), 2)
                self.assertEqual(text.count("[KO]"), 2)
                self.assertTrue(t.audio_queue.empty())
                self.assertTrue(t.text_queue.empty())
                self.assertTrue(logs.empty())
                self.assertTrue(t.error_queue.empty())

    def test_startup_failure_reaches_session_error_queue_and_stops_components(self):
        capture, stt, translator, logger = [Mock() for _ in range(4)]
        capture.start.side_effect = RuntimeError("device unavailable")
        session = MeetingSession(capture, stt, translator, logger, queue.Queue(),
                                 queue.Queue(), "unused.txt", "test")
        session.start()
        session._startup_thread.join(3)
        self.assertIn("device unavailable", session.error_queue.get_nowait())
        self.assertEqual(session.stop(), [])
        for worker in (capture, stt, translator, logger):
            worker.stop.assert_called_once()



class AudioAndLauncherTests(unittest.TestCase):
    def test_failed_output_restore_is_reported_and_can_be_retried(self):
        c = SystemAudioCapture(queue.Queue())
        c._original_output = 10
        c._multi_out_id = 30
        c._created_device = True
        c._run_switch = Mock(side_effect=lambda command, *args: "30")
        with self.assertRaisesRegex(RuntimeError, "복원"):
            c.stop()
        self.assertEqual(c._original_output, 10)
        self.assertNotIn(("destroy", "30"), [call.args for call in c._run_switch.call_args_list])
        c._run_switch.side_effect = lambda command, *args: "10"
        c.stop()
        c._run_switch.assert_any_call("destroy", "30")
        self.assertIsNone(c._original_output)

    def test_english_and_korean_multi_output_require_blackhole_subdevice(self):
        c = SystemAudioCapture(queue.Queue())
        devices = "10\tMulti-Output Device\n11\t다중 출력 기기\n12\tZoomTrans 캡처"

        def switch(command, *args):
            if command == "list":
                return devices
            if args[0] == "10":
                return "서브 장치:\n  2\tBlackHole 16ch\n  3\tSpeaker"
            return "서브 장치:\n  3\tSpeaker"

        c._run_switch = Mock(side_effect=switch)
        self.assertEqual(c._find_ams2_multi_output(), 10)
        c._run_switch.side_effect = lambda command, *args: (
            "11\t다중 출력 기기" if command == "list" else "서브 장치:\n  2\tBlackHole 16ch"
        )
        self.assertEqual(c._find_ams2_multi_output(), 11)
        c._run_switch.side_effect = lambda command, *args: (
            devices if command == "list" else "서브 장치:\n  3\tSpeaker"
        )
        self.assertIsNone(c._find_ams2_multi_output())

    def test_failed_output_verification_restores_original_and_destroys_temporary_device(self):
        c = SystemAudioCapture(queue.Queue())
        c._ensure_switch_binary = Mock()
        c._find_ams2_multi_output = Mock(return_value=None)
        c._find_device_id_by_name = Mock(return_value=20)
        c._run_switch = Mock(side_effect=lambda command, *args: "30" if command == "create-multi" else "10")
        with patch("time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "전환이 적용되지"):
                c.start()
        c._run_switch.assert_any_call("set", "10")
        c._run_switch.assert_any_call("destroy", "30")
        self.assertIsNone(c._original_output)

    def test_capture_stop_is_idempotent_and_closes_after_stop_error(self):
        c = AudioCapture(None, queue.Queue())
        stream = Mock()
        stream.stop.side_effect = RuntimeError("device disconnected")
        c._stream = stream
        with self.assertRaises(RuntimeError):
            c.stop()
        c.stop()
        stream.close.assert_called_once()

    def test_launcher_resolves_its_own_project_and_forwards_arguments(self):
        template = Path(__file__).resolve().parents[1] / "scripts/launcher.sh"
        # Override Bash's exec builtin: inspect the real launcher without
        # starting Python, Tk, models, or changing audio settings.
        harness = 'exec() { pwd; printf "%s\\n" "$@"; }; source "$0"'
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "project"
            script = project / "zoomtrans.app/Contents/MacOS/zoomtrans"
            script.parent.mkdir(parents=True)
            script.write_text(template.read_text())
            (project / "main.py").touch()
            result = subprocess.run(["/bin/bash", "-c", harness, str(script), "--silent"],
                                    cwd=d, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines()[0], str(project))
        self.assertTrue(result.stdout.endswith("main.py\n--silent\n"))

    def test_launcher_aborts_when_project_is_missing(self):
        script = Path(__file__).resolve().parents[1] / "scripts/launcher.sh"
        with tempfile.TemporaryDirectory() as d:
            copied = Path(d) / "zoomtrans.app/Contents/MacOS/zoomtrans"
            copied.parent.mkdir(parents=True)
            copied.write_text(script.read_text())
            result = subprocess.run(["/bin/bash", str(copied)], cwd="/tmp",
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("main.py not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
