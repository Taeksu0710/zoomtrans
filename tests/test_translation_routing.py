"""Routing, shared context and bounded fallback, without a local model server."""
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import httpx
import ollama
import main

from messages import Broadcaster, RuntimeStatus, SubtitleEvent
from translator import Translator, complexity_reason

LONG = " ".join(["word"] * 40)


def source(identity, text=LONG):
    return SubtitleEvent("en_only", identity, 1, text)


def response(text="완료된 번역", reason="stop"):
    return iter([{"message": {"content": text}, "done": True, "done_reason": reason}])


class RoutingTests(unittest.TestCase):
    def worker(self, events=(), replies=(), complex_model="large"):
        jobs, ui, logs = queue.Queue(), queue.Queue(), queue.Queue()
        bus = Broadcaster(ui, logs)
        for event in events:
            bus.put_nowait(event)
            jobs.put(event)
        with patch("translator.ollama.Client") as factory:
            worker = Translator("base", jobs, bus, complex_model=complex_model)
        worker._warmup = Mock()
        factory.return_value.chat.side_effect = replies
        return worker, factory.return_value, ui, logs, bus

    def test_routing_word_boundary_and_conditional_time_rules(self):
        self.assertEqual(complexity_reason(" ".join(["word"] * 39)), "short")
        self.assertEqual(complexity_reason(LONG), "length")
        for text in ("IF we wait UNTIL Friday, we can finish.",
                     "Unless we finish before Friday, wait.",
                     "Provided that we finish after noon, wait."):
            self.assertEqual(complexity_reason(text), "conditional_time", text)
        for text in ("Keep it online until Friday.", "The gift arrived before Friday.",
                     "If needed, postpone the release.", "We are not cancelling it."):
            self.assertEqual(complexity_reason(text), "short", text)

    def test_fixed_corpus_has_all_three_categories_and_expected_routes(self):
        cases = json.loads((Path(__file__).parent / "fixtures/translation_quality.json").read_text())
        self.assertEqual(len(cases), 24)
        for category in ("short", "long", "conditional"):
            self.assertEqual(sum(c["category"] == category for c in cases), 8)
        for case in cases:
            self.assertEqual(complexity_reason(case["source"]) == "short", case["category"] == "short")
            self.assertTrue(case["meaning_checks"])

    def test_model_disabled_and_same_model_never_duplicate_translation(self):
        for configured in (None, "none", "NONE", "base"):
            worker, client, _, _, _ = self.worker(replies=[response()], complex_model=configured)
            self.assertEqual(worker._translate(source("a")), "완료된 번역")
            self.assertEqual(client.chat.call_count, 1)
            self.assertEqual(client.chat.call_args.kwargs["model"], "base")
            self.assertFalse(worker._last_fallback)

    def test_cli_defaults_off_and_explicit_model_enables_for_this_launch(self):
        for extra, expected in (([], ["gemma4:e4b"]),
                                (["--complex-model", "gemma4:12b"], ["gemma4:e4b", "gemma4:12b"]),
                                (["--complex-model", "none"], ["gemma4:e4b"])):
            with tempfile.TemporaryDirectory() as directory:
                with patch("sys.argv", ["main.py", "--device", "unit-test-device",
                                        "--log-dir", directory, *extra]):
                    args = main.parse_args()
                with patch("translator.ollama.Client") as client:
                    client.return_value.chat.return_value = {"message": {"content": "네"}}
                    session = main.create_session(args, False)
                    try:
                        session.translator._warmup()
                        self.assertEqual([call.kwargs["model"] for call in
                                          client.return_value.chat.call_args_list], expected)
                        self.assertEqual(session.translator.complex_enabled, len(expected) == 2)
                        session.translator.set_complex_enabled(True)
                        self.assertEqual(session.translator._select_model(LONG)[0], "gemma4:12b")
                    finally:
                        session.performance.stop()

    def test_off_warmup_and_toggle_do_no_complex_model_io(self):
        worker, client, _, _, _ = self.worker(replies=[{"message": {"content": "네"}}])
        worker.set_complex_enabled(False)
        Translator._warmup(worker)
        self.assertEqual([c.kwargs["model"] for c in client.chat.call_args_list], ["base"])
        worker.set_complex_enabled(True)
        self.assertTrue(worker.complex_enabled)
        self.assertEqual(client.chat.call_count, 1)
        self.assertEqual(worker._select_model("Yes.")[0], "base")
        client.chat.side_effect = [response()]
        worker._translate(source("a"))
        self.assertEqual(client.chat.call_args.kwargs["model"], "large")

    def test_disable_during_base_warmup_skips_complex_warmup(self):
        worker, client, _, _, _ = self.worker()
        def warm(**kwargs):
            worker.set_complex_enabled(False)
            return {"message": {"content": "네"}}
        client.chat.side_effect = warm
        Translator._warmup(worker)
        self.assertEqual(client.chat.call_count, 1)

    def test_toggle_during_failure_keeps_base_fallback_and_shared_context(self):
        worker, client, _, _, _ = self.worker()
        worker._remember(source("previous", "Keep the backup."), "백업을 유지하세요.")
        def fail(**kwargs):
            worker.set_complex_enabled(False)
            raise httpx.ReadTimeout("slow")
        calls = 0
        def chat(**kwargs):
            nonlocal calls
            calls += 1
            return fail(**kwargs) if calls == 1 else response("완료")
        client.chat.side_effect = chat
        self.assertEqual(worker._translate(source("a")), "완료")
        self.assertEqual([c.kwargs["model"] for c in client.chat.call_args_list], ["large", "base"])
        self.assertTrue(worker._last_fallback)
        self.assertFalse(worker.complex_enabled)
        prompts = [c.kwargs["messages"] for c in client.chat.call_args_list]
        self.assertEqual(prompts[0], prompts[1])
        self.assertIn("Keep the backup.", prompts[1][1]["content"])

    def test_queued_jobs_use_new_setting_but_inflight_retry_keeps_model(self):
        events = [source("first"), source("second"), source("third")]
        worker, client, _, logs, _ = self.worker(events)
        entered, release = threading.Event(), threading.Event()
        def first_stream():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test worker was not released")
            yield {"message": {"content": "미완성"}, "done": True, "done_reason": "length"}
        def second_stream():
            worker.set_complex_enabled(True)
            yield {"message": {"content": "두 번째"}, "done": True, "done_reason": "stop"}
        client.chat.side_effect = [first_stream(), response("첫 번째"), second_stream(), response("세 번째")]
        worker._stop_event.set()  # Drain the existing FIFO, then exit.
        thread = threading.Thread(target=worker._run)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            worker.set_complex_enabled(False)
            self.assertFalse(worker.complex_enabled)
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([c.kwargs["model"] for c in client.chat.call_args_list],
                         ["large", "large", "base", "large"])
        self.assertEqual([e.utterance_id for e in logs.queue if e.kind == "en_ko"],
                         ["first", "second", "third"])

    def test_mixed_queue_preserves_order_and_cross_model_native_context(self):
        events = [source("short", "Use the primary server."),
                  replace(source("native", "백업 서버는 사용하지 마세요."), kind="native", language="ko"),
                  source("long"), source("next", "Keep it online.")]
        worker, client, ui, logs, _ = self.worker(events, [response("주 서버를 사용하세요."),
                                                        response("긴 문장 번역"), response("계속 켜 두세요.")])
        worker._stop_event.set()
        worker._run()
        calls = client.chat.call_args_list
        self.assertEqual([call.kwargs["model"] for call in calls], ["base", "large", "base"])
        context = json.loads(calls[1].kwargs["messages"][1]["content"])["previous_dialogue"]
        self.assertEqual([entry["original"] for entry in context], [events[0].text, events[1].text])
        self.assertEqual(context[0]["translation"], "주 서버를 사용하세요.")
        next_context = json.loads(calls[2].kwargs["messages"][1]["content"])["previous_dialogue"]
        self.assertEqual(next_context[-1]["translation"], "긴 문장 번역")
        done = [event for event in logs.queue if event.kind == "en_ko"]
        self.assertEqual([event.utterance_id for event in done], ["short", "long", "next"])
        self.assertEqual([event.translation_model for event in done], ["base", "large", "base"])
        self.assertTrue(any(isinstance(event, RuntimeStatus) and event.state == "busy_complex" for event in ui.queue))

    def test_timeout_empty_and_missing_model_fallback_once_without_partials(self):
        for failure in (httpx.ReadTimeout("slow"), response(""),
                        ollama.ResponseError("model missing", status_code=404)):
            worker, client, ui, logs, _ = self.worker([source("a")], [failure, response("기본 모델 결과")])
            worker._stop_event.set()
            worker._run()
            self.assertEqual([call.kwargs["model"] for call in client.chat.call_args_list], ["large", "base"])
            done = [event for event in logs.queue if event.kind == "en_ko"]
            self.assertEqual(len(done), 1)
            self.assertEqual(done[0].translation, "기본 모델 결과")
            self.assertTrue(done[0].translation_fallback)
            self.assertEqual(done[0].translation_model, "base")
            self.assertTrue(any(isinstance(event, SubtitleEvent) and event.kind == "status" for event in ui.queue))
            self.assertFalse(any(isinstance(event, SubtitleEvent) and event.kind == "ko_partial" for event in ui.queue))

    def test_partial_failed_complex_generation_is_never_published(self):
        def broken():
            yield {"message": {"content": "불완전한 번역"}}
            raise httpx.ReadTimeout("slow")
        worker, _, ui, logs, _ = self.worker([source("a")], [broken(), response("최종 번역")])
        worker._stop_event.set()
        worker._run()
        self.assertFalse(any(isinstance(event, SubtitleEvent) and event.translation == "불완전한 번역" for event in ui.queue))
        self.assertEqual([event.translation for event in logs.queue if event.kind == "en_ko"], ["최종 번역"])

    def test_length_retry_stays_on_complex_model(self):
        worker, client, _, _, _ = self.worker(replies=[response("잘림", "length"), response()])
        self.assertEqual(worker._translate(source("a")), "완료된 번역")
        self.assertEqual([c.kwargs["model"] for c in client.chat.call_args_list], ["large", "large"])
        self.assertEqual([c.kwargs["options"]["num_predict"] for c in client.chat.call_args_list], [512, 1024])
        self.assertFalse(worker._last_fallback)

    def test_twice_truncated_complex_result_falls_back_without_adding_context(self):
        worker, client, _, _, _ = self.worker(replies=[response("잘림", "length"),
                                                     response("또 잘림", "length"), response("완료")])
        self.assertEqual(worker._translate(source("a")), "완료")
        self.assertEqual([c.kwargs["model"] for c in client.chat.call_args_list], ["large", "large", "base"])
        prompts = [c.kwargs["messages"] for c in client.chat.call_args_list]
        self.assertEqual(prompts[0], prompts[2])
        self.assertFalse(worker._context)
        self.assertTrue(worker._last_fallback)

    def test_both_models_fail_keep_original_and_error_context(self):
        worker, client, ui, logs, _ = self.worker([source("a"), source("b", "Next sentence.")],
            [RuntimeError("large failed"), RuntimeError("base failed"), response()])
        worker._stop_event.set()
        worker._run()
        self.assertEqual([e.utterance_id for e in logs.queue if e.kind == "en_only"], ["a", "b"])
        self.assertEqual([e.utterance_id for e in logs.queue if e.kind == "en_ko"], ["b"])
        failed = next(e for e in ui.queue if isinstance(e, SubtitleEvent) and e.kind == "en_error")
        self.assertTrue(failed.translation_fallback)
        self.assertEqual(failed.translation_model, "base")
        context = json.loads(client.chat.call_args.kwargs["messages"][1]["content"])["previous_dialogue"]
        self.assertEqual(context[0]["original"], LONG)
        self.assertEqual(context[0]["translation"], "")

    def test_missing_model_is_not_requested_repeatedly(self):
        worker, client, _, _, _ = self.worker(replies=[ollama.ResponseError("missing", status_code=404),
                                                     response(), response()])
        worker._translate(source("a"))
        worker._translate(source("b"))
        self.assertEqual([c.kwargs["model"] for c in client.chat.call_args_list], ["large", "base", "base"])

    def test_obsolete_complex_revision_closes_response_and_never_falls_back(self):
        original = source("a")
        worker, client, _, _, bus = self.worker([original])
        closed = []
        def stream():
            try:
                yield {"message": {"content": "부분"}}
                bus.put_nowait(replace(original, revision=2, text="Corrected"))
                yield {"message": {"content": "오래된 결과"}}
            finally:
                closed.append(True)
        client.chat.side_effect = [stream()]
        self.assertEqual(worker._translate(original), "")
        self.assertEqual(client.chat.call_count, 1)
        self.assertEqual(closed, [True])
        self.assertFalse(worker._last_fallback)

    def test_thinking_support_is_separate_for_each_model(self):
        worker, client, _, _, _ = self.worker(replies=[
            ollama.ResponseError("does not support thinking", status_code=400), response(), response()])
        worker._translate(source("a"))
        worker._translate(source("b", "Yes."))
        calls = client.chat.call_args_list
        self.assertIs(calls[0].kwargs["think"], False)
        self.assertNotIn("think", calls[1].kwargs)
        self.assertIs(calls[2].kwargs["think"], False)

    def test_warmup_prepares_each_distinct_model_with_matching_options(self):
        worker, client, _, _, _ = self.worker(replies=[{"message": {"content": "네"}}, {"message": {"content": "네"}}])
        worker._warmup = Translator._warmup.__get__(worker)
        worker._warmup()
        calls = client.chat.call_args_list
        self.assertEqual([c.kwargs["model"] for c in calls], ["base", "large"])
        self.assertEqual(calls[0].kwargs["options"], calls[1].kwargs["options"])
        self.assertEqual(calls[1].kwargs["options"]["num_ctx"], 8192)
        self.assertIs(calls[1].kwargs["think"], False)

    def test_missing_complex_warmup_keeps_base_ready_and_warns(self):
        worker, _, ui, _, _ = self.worker(replies=[{"message": {"content": "네"}},
                                                 ollama.ResponseError("missing", status_code=404)])
        worker._warmup = Translator._warmup.__get__(worker)
        worker._warmup()
        states = [e.state for e in ui.queue if isinstance(e, RuntimeStatus)]
        self.assertEqual(states[-1], "ready")
        self.assertTrue(any(isinstance(e, SubtitleEvent) and e.kind == "status" for e in ui.queue))
        self.assertIn("large", worker._unavailable_models)

    def test_shutdown_skips_unneeded_warmup_and_records_actual_model_metrics(self):
        worker, client, _, _, _ = self.worker()
        worker._stop_event.set()
        Translator._warmup(worker)
        client.chat.assert_not_called()
        worker, _, _, logs, _ = self.worker([source("a")], [response()])
        metrics = []
        worker.performance = lambda stage, **values: metrics.append({"stage": stage, **values})
        worker._stop_event.set()
        worker._run()
        entry = next(entry for entry in metrics if entry["stage"] == "translation")
        self.assertEqual(entry["model"], "large")
        self.assertFalse(entry["fallback"])
        self.assertEqual(entry["routing_reason"], "length")
        self.assertNotIn(LONG, json.dumps(metrics))
        self.assertEqual(next(event for event in logs.queue if event.kind == "en_ko").translation_model, "large")


if __name__ == "__main__":
    unittest.main()
