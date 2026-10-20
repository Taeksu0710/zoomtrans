"""Deterministic sentence and context regressions without audio hardware/models."""
import json
import queue
import unittest
import time
import tempfile
from pathlib import Path
import ollama
import httpx
from performance import PerformanceLogger
from audio_capture import AudioCapture, CHUNK_SECONDS
from dataclasses import replace
from unittest.mock import Mock, patch

import numpy as np

from messages import AudioChunk, Broadcaster, SubtitleEvent
from sentence_boundary import is_incomplete, split_sentences
from transcriber import Transcriber
from translator import Translator


def speech(seconds=.5):
    return np.full(int(16000 * seconds), .1, dtype=np.float32)


def silence(seconds):
    return np.zeros(int(16000 * seconds), dtype=np.float32)


class SentenceTests(unittest.TestCase):
    def run_audio(self, chunks, texts):
        q, jobs, ui = queue.Queue(), queue.Queue(), queue.Queue()
        t = Transcriber("small", q, jobs, ui)
        t._warmup = Mock()
        t._calibrate_silence_threshold = Mock(return_value=.005)
        for chunk in chunks:
            q.put(chunk)
        t._stop_event.set()
        with patch("transcriber.mlx_whisper.transcribe", side_effect=[
            {"text": text, "language": "en"} for text in texts
        ]) as recognize:
            t._run()
        return t, list(jobs.queue), list(ui.queue), recognize

    def test_complete_sentence_starts_at_six_tenths_pause(self):
        _, jobs, _, recognize = self.run_audio([speech(), silence(.6)], ["Yes."])
        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0].incomplete)
        self.assertEqual(jobs[0].finish_reason, "sentence")
        self.assertEqual(len(recognize.call_args.args[0]), 17600)

    def test_incomplete_clause_retains_audio_and_identity(self):
        _, jobs, ui, recognize = self.run_audio(
            [speech(), silence(.6), speech(), silence(.6)],
            ["If we postpone the launch.", "If we postpone the launch, we will miss Friday."],
        )
        self.assertEqual(len(jobs), 1)
        self.assertIn("miss Friday", jobs[0].text)
        preview = next(e for e in ui if isinstance(e, SubtitleEvent) and not e.final)
        self.assertEqual(preview.utterance_id, jobs[0].utterance_id)
        self.assertGreater(jobs[0].revision, preview.revision)
        self.assertEqual(len(recognize.call_args_list[1].args[0]), 35200)

    def test_brief_pause_does_not_split_short_speech(self):
        _, jobs, _, recognize = self.run_audio(
            [speech(2), silence(.3), speech(2), silence(.6)],
            ["We will not pay 25 dollars before October 31."],
        )
        self.assertEqual(recognize.call_count, 1)
        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0].incomplete)
        self.assertIn("not pay 25", jobs[0].text)

    def test_eight_second_scan_keeps_unfinished_audio_instead_of_forcing_translation(self):
        _, jobs, ui, recognize = self.run_audio(
            [speech(8), silence(.3)],
            ["This is a long explanation", "This is a long explanation"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].finish_reason, "shutdown")
        self.assertEqual(len(recognize.call_args_list[0].args[0]), 128000)
        self.assertEqual(len(recognize.call_args_list[1].args[0]), 132800)
        self.assertTrue(any(isinstance(e, SubtitleEvent) and not e.final for e in ui))

    def test_twenty_five_second_sentence_is_not_cut_at_ten_seconds(self):
        text = "If we postpone the launch, we will miss Friday."
        _, jobs, _, recognize = self.run_audio([speech(25)],
            ["If we postpone"] * 5 + [text])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].text, text)
        self.assertEqual(jobs[0].finish_reason, "shutdown")
        self.assertEqual([len(c.args[0]) for c in recognize.call_args_list],
                         [128000, 192000, 256000, 320000, 384000, 400000])

    @staticmethod
    def aligned_result(text, spacing=.6):
        words = [{"word": " " + word, "start": i * spacing, "end": i * spacing + .4}
                 for i, word in enumerate(text.split())]
        return {"text": text, "language": "en", "segments": [{"words": words}]}

    def test_scan_emits_finished_prefix_and_retains_unfinished_audio(self):
        t = Transcriber("small", queue.Queue(), queue.Queue(), queue.Queue())
        t._warmup = Mock()
        t._calibrate_silence_threshold = Mock(return_value=.005)
        t.audio_queue.put(speech(8))
        t._stop_event.set()
        first = self.aligned_result("The first sentence is done. If we postpone")
        last = {"text": "If we postpone the launch, we will miss Friday.", "language": "en"}
        with patch("transcriber.mlx_whisper.transcribe", side_effect=[first, last]) as recognize:
            t._run()
        jobs = list(t.text_queue.queue)
        self.assertEqual([job.text for job in jobs],
                         ["The first sentence is done.", "If we postpone the launch, we will miss Friday."])
        self.assertNotEqual(jobs[0].utterance_id, jobs[1].utterance_id)
        self.assertFalse(jobs[0].incomplete)
        cut = int((first["segments"][0]["words"][4]["end"] +
                   first["segments"][0]["words"][5]["start"]) / 2 * 16000)
        self.assertEqual(len(recognize.call_args_list[1].args[0]), 128000 - cut)

    def test_scan_keeps_all_audio_if_word_alignment_is_missing_or_invalid(self):
        for invalid in (None, float("nan"), 100.0):
            t = Transcriber("small", queue.Queue(), queue.Queue(), queue.Queue())
            result = self.aligned_result("The first sentence is done. Another clause")
            if invalid is None:
                result["segments"][0]["words"].pop(0)
            else:
                result["segments"][0]["words"][4]["end"] = invalid
            t._scan_mode = True
            self.assertFalse(t._finish_scan(result["text"], "en", result, 128000))
            self.assertIsNone(t._retain_from)
            self.assertTrue(t.text_queue.empty())

    def test_window_rotation_carries_text_without_translating_unfinished_sentence(self):
        t = Transcriber("small", queue.Queue(), queue.Queue(), queue.Queue())
        t._warmup = Mock()
        t._calibrate_silence_threshold = Mock(return_value=.005)
        t.audio_queue.put(speech(30))
        t._stop_event.set()
        window = self.aligned_result(" ".join(f"word{i}" for i in range(14)), spacing=2)
        tail = {"text": "word13 last segment finishes.", "language": "en"}
        results = [{"text": "If we postpone", "language": "en"}] * 5 + [window, tail]
        with patch("transcriber.mlx_whisper.transcribe", side_effect=results) as recognize:
            t._run()
        jobs = list(t.text_queue.queue)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].text,
                         " ".join(f"word{i}" for i in range(14)) + " last segment finishes.")
        self.assertNotIn(jobs[0].finish_reason, {"audio_limit", "phrase_limit"})
        self.assertLess(len(recognize.call_args_list[-1].args[0]), 160000)

    def test_silence_fallback_reuses_candidate_at_one_point_two_seconds(self):
        _, jobs, _, recognize = self.run_audio(
            [speech(), silence(.6), silence(.6)], ["If we postpone"])
        self.assertEqual(recognize.call_count, 1)
        self.assertTrue(jobs[0].incomplete)
        self.assertEqual(jobs[0].finish_reason, "silence_limit")

    def test_shutdown_flushes_short_last_speech(self):
        _, jobs, _, recognize = self.run_audio([speech(.2)], ["Yes."])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(recognize.call_args.args[0]), 3200)
        self.assertEqual(jobs[0].finish_reason, "shutdown")

    def test_empty_capture_queue_does_not_fabricate_silence(self):
        q = queue.Queue()
        t = Transcriber("small", q, queue.Queue(), queue.Queue())
        t._warmup = Mock()
        t._calibrate_silence_threshold = Mock(return_value=.005)
        t._transcribe = Mock()
        calls = 0
        def get(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return speech()
            self.assertEqual(t._transcribe.call_count, 0)
            if calls == 8:
                t._stop_event.set()
            raise queue.Empty
        q.get = get
        t._run()
        t._transcribe.assert_called_once()

    def test_multi_sentence_jobs_have_unique_ids_and_keep_preview_id(self):
        t = Transcriber("small", queue.Queue(), queue.Queue(), queue.Queue())
        t._publish_text("Ready. We should", final=False)
        preview = t.subtitle_queue.get_nowait()
        t._candidate = ("Ready. We should proceed. Do not change the date!", "en")
        t._finish_candidate()
        jobs = list(t.text_queue.queue)
        self.assertEqual([e.text for e in jobs], ["Ready.", "We should proceed.", "Do not change the date!"])
        self.assertEqual(jobs[0].utterance_id, preview.utterance_id)
        self.assertGreater(jobs[0].revision, preview.revision)
        self.assertEqual(len(set(e.utterance_id for e in jobs)), 3)

    def test_splitter_preserves_abbreviations_numbers_dates_and_quotes(self):
        cases = [
            ('Dr. Smith paid $3.50 on 10.31.2026. We did not.',
             ['Dr. Smith paid $3.50 on 10.31.2026.', 'We did not.']),
            ('J. Smith met the U.S. team at 9 a.m. on October 31. 2026. Yes!',
             ['J. Smith met the U.S. team at 9 a.m. on October 31. 2026.', 'Yes!']),
            ('He said "No." We agreed.', ['He said "No."', 'We agreed.']),
            ('첫 문장입니다. 다음 문장입니다.', ['첫 문장입니다.', '다음 문장입니다.']),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(split_sentences(source), expected)
                self.assertEqual(" ".join(expected), source)

    def test_incomplete_heuristics_preserve_short_answers_and_full_conditionals(self):
        for text in ("If we postpone the launch.", "We should", "This is for the", "Not"):
            self.assertTrue(is_incomplete(text), text)
        for text in ("Yes.", "No.", "We must not postpone it.",
                     "If it rains we will stay home.", "If it rains, we will stay home."):
            self.assertFalse(is_incomplete(text), text)
        self.assertFalse(is_incomplete("좋습니다.", "ko"))

    def test_asr_period_does_not_separate_condition_from_its_main_clause(self):
        t = Transcriber("small", queue.Queue(), queue.Queue(), queue.Queue())
        t._candidate = ("If we postpone the launch. We will miss Friday.", "en")
        t._force_boundary = "shutdown"
        t._finish_candidate()
        self.assertEqual(t.text_queue.qsize(), 1)
        self.assertEqual(t.text_queue.get_nowait().text,
                         "If we postpone the launch, We will miss Friday.")
        self.assertTrue(is_incomplete(
            "If we postpone the launch, until the security review has finished."))

    def test_capture_timestamp_survives_calibration_and_buffering(self):
        stamp = 12345.0
        _, jobs, _, _ = self.run_audio(
            [AudioChunk(speech(), stamp), AudioChunk(silence(.6), stamp + .6)], ["Yes."])
        self.assertEqual(jobs[0].speech_ended_at, stamp)
        self.assertGreater(jobs[0].queued_at, 0)
        self.assertGreaterEqual(jobs[0].stt_seconds, 0)
        self.assertAlmostEqual(jobs[0].audio_seconds, 1.1)


class ContextTests(unittest.TestCase):
    def make_worker(self, events, responses):
        jobs, ui, logs = queue.Queue(), queue.Queue(), queue.Queue()
        bus = Broadcaster(ui, logs)
        for event in events:
            bus.put_nowait(event)
            jobs.put(event)
        with patch("translator.ollama.Client") as client:
            t = Translator("test", jobs, bus)
        t._warmup = Mock()
        client.return_value.chat.side_effect = responses
        return t, client.return_value, ui, logs

    def original(self, identity, text="We will not pay 25 dollars on October 31."):
        return SubtitleEvent("en_only", identity, 1, text)

    @staticmethod
    def response(text="완성된 번역", reason="stop"):
        return iter([{"message": {"content": text}, "done": True, "done_reason": reason}])

    def test_live_backlog_keeps_fifo_and_failed_native_context(self):
        events = [self.original("fail"),
                  replace(self.original("native", "일정은 유지합니다."), kind="native", language="ko"),
                  self.original("next")]
        t, client, ui, _ = self.make_worker(events, [RuntimeError("offline"), self.response()])
        # End after the actual live loop consumes the last job: shutdown mode
        # must not be what prevents the old latest-job skipping behavior.
        original_translate = t._translate
        def translate(event):
            try:
                return original_translate(event)
            finally:
                if event.utterance_id == "next":
                    t._stop_event.set()
        t._translate = translate
        t._run()
        prompt = json.loads(client.chat.call_args_list[1].kwargs["messages"][1]["content"])
        self.assertEqual([p["original"] for p in prompt["previous_dialogue"]],
                         [events[0].text, events[1].text])
        self.assertEqual(prompt["current"]["original"], events[2].text)
        self.assertEqual([e.utterance_id for e in ui.queue if isinstance(e, SubtitleEvent)
                          and e.kind == "en_ko"], ["next"])

    def test_tokens_remain_private_until_successful_retry(self):
        t, client, ui, logs = self.make_worker([self.original("a")], [
            self.response("잘린 번역", "length"), self.response("완성된 번역")])
        t._stop_event.set()
        t._run()
        self.assertEqual([c.kwargs["options"]["num_predict"] for c in client.chat.call_args_list], [512, 1024])
        self.assertFalse(any(e.kind == "ko_partial" for e in ui.queue if isinstance(e, SubtitleEvent)))
        self.assertEqual([e.translation for e in logs.queue if e.kind == "en_ko"], ["완성된 번역"])

    def test_second_truncation_is_error_and_original_is_context(self):
        t, _, ui, logs = self.make_worker([self.original("a")], [
            self.response("잘림", "length"), self.response("또 잘림", "length")])
        t._stop_event.set()
        t._run()
        self.assertTrue(any(e.kind == "en_error" for e in ui.queue if isinstance(e, SubtitleEvent)))
        self.assertFalse(any(e.kind == "en_ko" for e in logs.queue))
        self.assertEqual(t._context[0]["original"], self.original("a").text)
        self.assertEqual(t._context[0]["translation"], "")

    def test_six_utterance_and_character_limits(self):
        t, _, _, _ = self.make_worker([], [])
        for n in range(9):
            t._remember(self.original(str(n), str(n)), "번역")
        self.assertEqual(len(t._context), 6)
        self.assertEqual(t._context[0]["original"], "3")
        t._remember(self.original("large", "x" * 5000), "y" * 5000)
        self.assertLessEqual(sum(len(e["original"]) + len(e["translation"]) for e in t._context), 4000)

    def test_incomplete_metadata_and_numeric_source_reach_prompt(self):
        event = replace(self.original("a"), incomplete=True, finish_reason="audio_limit")
        t, client, _, _ = self.make_worker([event], [self.response()])
        t._stop_event.set()
        t._run()
        current = json.loads(client.chat.call_args.kwargs["messages"][1]["content"])["current"]
        self.assertEqual(current["original"], event.text)
        self.assertTrue(current["incomplete"])
        self.assertEqual(current["boundary"], "audio_limit")

    def test_thinking_disabled_and_context_limit_match_warmup(self):
        t, client, _, _ = self.make_worker([], [])
        client.chat.side_effect = None
        client.chat.return_value = {"message": {"content": "안녕하세요."}}
        t._warmup = Translator._warmup.__get__(t)
        t._warmup()
        warmup = dict(client.chat.call_args.kwargs)
        client.chat.return_value = self.response()
        self.assertEqual(t._translate(self.original("a")), "완성된 번역")
        actual = client.chat.call_args.kwargs
        self.assertIs(warmup["think"], False)
        self.assertIs(actual["think"], False)
        self.assertEqual(warmup["options"], actual["options"])
        self.assertEqual(actual["options"]["num_ctx"], 8192)

    def test_unsupported_thinking_on_lazy_stream_retries_once(self):
        def unsupported():
            raise ollama.ResponseError("model does not support thinking", status_code=400)
            yield
        t, client, _, _ = self.make_worker([], [unsupported(), self.response()])
        self.assertEqual(t._translate(self.original("a")), "완성된 번역")
        self.assertEqual(client.chat.call_count, 2)
        self.assertIs(client.chat.call_args_list[0].kwargs["think"], False)
        self.assertNotIn("think", client.chat.call_args_list[1].kwargs)

    def test_unrelated_errors_and_empty_output_do_not_retry(self):
        for response in (ollama.ResponseError("out of memory", status_code=500),
                         httpx.ReadTimeout("slow"), self.response("")):
            t, client, _, _ = self.make_worker([], [response])
            try:
                result = t._translate(self.original("a"))
                self.assertEqual(result, "")
            except (ollama.ResponseError, httpx.ReadTimeout):
                pass
            self.assertEqual(client.chat.call_count, 1)

    def test_metrics_report_queue_wait_and_ollama_durations_without_content(self):
        original = replace(self.original("a"), queued_at=time.monotonic() - 2,
                           speech_ended_at=time.monotonic() - 4)
        response = iter([{"message": {"content": "번역문"}, "done": True, "done_reason": "stop",
                          "prompt_eval_duration": 100000000, "eval_duration": 200000000,
                          "eval_count": 5}])
        t, _, _, _ = self.make_worker([original], [response])
        metrics = []
        t.performance = lambda stage, **values: metrics.append({"stage": stage, **values})
        t._stop_event.set()
        t._run()
        generation, translation = metrics
        self.assertEqual(generation["prompt_eval_duration"], 100000000)
        self.assertEqual(generation["eval_duration"], 200000000)
        self.assertGreaterEqual(translation["queue_wait_seconds"], 2)
        self.assertGreaterEqual(translation["speech_to_result_seconds"], 4)
        self.assertTrue(translation["success"])
        self.assertNotIn("번역문", json.dumps(metrics, ensure_ascii=False))
        self.assertNotIn(original.text, json.dumps(metrics))


class CaptureAndDiagnosticsTests(unittest.TestCase):
    def test_capture_keeps_timestamp_and_owned_audio_when_queue_is_full(self):
        q = queue.Queue(maxsize=1)
        capture = AudioCapture(None, q)
        indata = np.full((1600, 1), .1, dtype=np.float32)
        capture._callback(indata, 1600, None, None)
        first = q.queue[0]
        capture._callback(indata, 1600, None, None)
        latest = q.get_nowait()
        indata[:] = 0
        self.assertIsInstance(latest, AudioChunk)
        self.assertGreaterEqual(latest.captured_at, first.captured_at)
        self.assertTrue(np.all(latest.samples > 0))
        self.assertEqual(CHUNK_SECONDS, .1)

    def test_calibration_keeps_timestamped_audio(self):
        t = Transcriber("small", queue.Queue(), queue.Queue(), queue.Queue())
        original = AudioChunk(speech(), 123.0)
        def get(**kwargs):
            t._stop_event.set()
            return original
        with patch.object(t.audio_queue, "get", side_effect=get):
            t._calibrate_silence_threshold()
        self.assertIs(t._calibration_chunks[0], original)

    def test_metrics_are_separate_json_lines_and_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "performance.jsonl"
            logger = PerformanceLogger(path)
            logger("recognition", seconds=.25, audio_seconds=2)
            logger("translation", seconds=.5, queue_wait_seconds=.1)
            logger.stop()
            logger.stop()
            logger("ignored", seconds=1)
            data = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([entry["stage"] for entry in data], ["recognition", "translation"])
            self.assertTrue(all("text" not in entry and "translation" not in entry for entry in data))



if __name__ == "__main__":
    unittest.main()
