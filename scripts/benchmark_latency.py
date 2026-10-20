#!/usr/bin/env python3
"""Isolated local latency/quality samples, using installed macOS TTS voices.

Run each version in its own process, with identical audio in --samples-dir.
--source-dir can point to a saved old source tree; --legacy enables its LLM
boundary checker. No audio device, meeting recording, UI or cloud API is used.
Latency starts at the simulated speech end, includes actual silence waiting,
ASR, boundary checking and all Korean generation; excludes playback, model
warmup, UI painting and concurrent queue backlog. Small-sample p95 is descriptive.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import time
import wave

os.environ.setdefault("HF_HUB_OFFLINE", "1")

REFERENCES = [
    "Yes.",
    "We will not postpone the release.",
    "The budget is twenty five dollars, not fifty.",
    "Please send the report by October thirty first.",
    "If we postpone the launch, we will miss Friday.",
    "Doctor Smith approved version three point five. Do not change it.",
]
LONG_REFERENCE = (
    "If we postpone the launch until the engineering team has completed the security review "
    "and the finance team has confirmed that the revised budget includes the additional testing costs "
    "and the customer support team has prepared the updated instructions for the people who are "
    "moving from the previous version to the new service and every regional manager has checked "
    "that the proposed schedule will work for their local customers and the operations team has "
    "verified that the backup system can restore all of the important customer records without "
    "losing any recent changes, then we will release the product on October thirty first "
    "and we will not charge existing customers an additional fifty dollars."
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--samples-dir", type=Path, default=Path("output/latency/samples"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--long-context", action="store_true",
                        help="Offline replay of one long conditional; elapsed time is NOT live latency")
    parser.add_argument("--regenerate", action="store_true", help="Regenerate only the synthetic test WAVs")
    parser.add_argument("--model", default="gemma4:e4b")
    parser.add_argument("--complex-model", default=None,
                        help="Optional selective model; omitted means base-only comparison")
    parser.add_argument("--whisper-model", default="large-v3")
    args = parser.parse_args()
    if args.legacy and args.complex_model:
        parser.error("The saved legacy source does not support --complex-model")
    sys.path.insert(0, str(args.source_dir.resolve()))
    import numpy as np
    from transcriber import Transcriber, SILENCE_TRIGGER, FORCE_SILENCE, SAMPLE_RATE
    from translator import Translator
    args.samples_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    inputs = []
    for index, reference in enumerate([LONG_REFERENCE] if args.long_context else REFERENCES):
        path = args.samples_dir / f"{'long' if args.long_context else 'sample'}-{index + 1}.wav"
        if args.regenerate or not path.exists():
            subprocess.run(["/usr/bin/say", "-v", "Samantha", "-r", "180",
                            "--file-format=WAVE", "--data-format=LEI16@16000",
                            "-o", str(path), reference], check=True)
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
                raise ValueError("Expected mono 16kHz 16-bit PCM audio")
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
        active = np.flatnonzero(np.abs(audio) > .01)
        if not len(active):
            raise ValueError(f"No speech in {path}")
        audio = audio[max(0, active[0] - 800):min(len(audio), active[-1] + 800)]
        inputs.append((reference, audio, hashlib.sha256(path.read_bytes()).hexdigest()))

    class MeasuredClient:
        def __init__(self, client):
            self.client = client
            self.calls = []
        def chat(self, **kwargs):
            if not kwargs.get("stream"):
                return self.client.chat(**kwargs)
            def generate():
                started = time.monotonic()
                response = self.client.chat(**kwargs)
                final = {}
                try:
                    for chunk in response:
                        if chunk.get("done"):
                            final = {name: chunk.get(name) for name in (
                                "done_reason", "load_duration", "prompt_eval_duration", "eval_duration",
                                "prompt_eval_count", "eval_count")}
                        yield chunk
                finally:
                    close = getattr(response, "close", None)
                    if close:
                        close()
                    self.calls.append({"model": kwargs["model"],
                                       "wall_seconds": time.monotonic() - started, **final})
            return generate()
        def close(self):
            close = getattr(self.client, "close", None) or getattr(self.client._client, "close", None)
            if close:
                close()

    jobs, ui = queue.Queue(), queue.Queue()
    checker = None
    if args.legacy:
        checker = importlib.import_module("sentence_boundary").SentenceBoundaryChecker(args.model)
        recognizer = Transcriber(args.whisper_model, queue.Queue(), jobs, ui, boundary_checker=checker)
    else:
        recognizer = Transcriber(args.whisper_model, queue.Queue(), jobs, ui)
    translator = (Translator(args.model, jobs, ui) if args.legacy else
                  Translator(args.model, jobs, ui, complex_model=args.complex_model))
    translator._client = MeasuredClient(translator._client)
    rows = []
    result = {"source_dir": str(args.source_dir.resolve()), "legacy": args.legacy,
              "model": args.model, "complex_model": args.complex_model,
              "whisper_model": args.whisper_model,
              "method": ("Offline preloaded-audio pipeline replay, not real-time latency. "
                         if args.long_context else "") + __doc__, "cases": rows}
    try:
        recognizer._warmup()
        if checker:
            checker.is_complete("Yes.")
        translator._warmup()
        for reference, audio, digest in inputs:
            recognizer._reset_segment()
            if hasattr(recognizer, "_audio_seconds"):
                recognizer._audio_seconds = len(audio) / SAMPLE_RATE + SILENCE_TRIGGER
            translator._client.calls.clear()
            started = time.monotonic()
            if not args.long_context:
                time.sleep(SILENCE_TRIGGER)
            padded = np.concatenate([audio, np.zeros(round(SAMPLE_RATE * SILENCE_TRIGGER), dtype=np.float32)])
            stt_started = time.monotonic()
            if args.long_context:
                recognizer._warmup = lambda: None
                recognizer._calibrate_silence_threshold = lambda: .005
                for offset in range(0, len(padded), 1600):
                    recognizer.audio_queue.put(padded[offset:offset + 1600])
                recognizer._stop_event.set()
                recognizer._run()
                complete = True
            else:
                complete = recognizer._transcribe(padded, .005, float(np.max(np.abs(audio))))
            stt_and_boundary = time.monotonic() - stt_started
            if complete is False:
                # Silence has continued while inference was running.
                time.sleep(max(0, FORCE_SILENCE - (time.monotonic() - started)))
                recognizer._force_boundary = "silence_limit"
                recognizer._transcribe(padded, .005, float(np.max(np.abs(audio))))
            originals, translations, errors, reasons, models, fallbacks = [], [], [], [], [], []
            while not jobs.empty():
                event = jobs.get_nowait()
                originals.append(event.text)
                reasons.append(event.finish_reason)
                try:
                    korean = translator._translate(event)
                    translations.append(korean)
                    models.append(getattr(translator, "_last_model", args.model))
                    fallbacks.append(getattr(translator, "_last_fallback", False))
                    if hasattr(translator, "_remember"):
                        translator._remember(event, korean)
                    if not korean:
                        errors.append("empty translation")
                except Exception as exc:
                    errors.append(str(exc))
            row = {"reference": reference, "audio_sha256": digest,
                   "audio_seconds": len(audio) / SAMPLE_RATE,
                   "recognized": originals, "translations": translations, "errors": errors,
                   "translation_models": models, "translation_fallbacks": fallbacks,
                   ("offline_replay_processing_seconds" if args.long_context else
                    "speech_end_to_result_seconds"): time.monotonic() - started,
                   "finish_reasons": reasons,
                   "stt_and_boundary_seconds": stt_and_boundary,
                   "translation_calls": list(translator._client.calls)}
            if not originals:
                row["errors"].append("no finalized recognition")
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        elapsed_key = ("offline_replay_processing_seconds" if args.long_context else
                       "speech_end_to_result_seconds")
        successful = [r[elapsed_key] for r in rows if not r["errors"]]
        generations = [sum(c["wall_seconds"] for c in r["translation_calls"]) for r in rows if not r["errors"]]
        result["summary"] = {"cases": len(rows), "successful": len(successful),
                             "timing_measure": elapsed_key,
                             "latency_median_seconds": (float(np.median(successful))
                                                        if successful and not args.long_context else None),
                             "latency_p95_seconds": (float(np.percentile(successful, 95))
                                                     if successful and not args.long_context else None),
                             "translation_median_seconds": float(np.median(generations)) if generations else None}
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result["summary"]), flush=True)
    finally:
        translator._client.close()
        if checker:
            checker.close()


if __name__ == "__main__":
    main()
