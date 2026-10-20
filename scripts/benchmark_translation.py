#!/usr/bin/env python3
"""Compare base-only versus selective local translation on identical fixed text.

Quality must be reviewed using meaning_checks, never inferred from a successful
HTTP response. Models and the source corpus are fixed; neither audio recognition
nor real meeting data is involved. No model downloads are performed.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import queue
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from messages import SubtitleEvent
from translator import Translator, complexity_reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", default="gemma4:e4b")
    parser.add_argument("--complex-model", default="gemma4:12b")
    parser.add_argument("--corpus", type=Path, default=ROOT / "tests/fixtures/translation_quality.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output/quality/translation-comparison.json")
    parser.add_argument("--case", help="Run only one corpus ID, for a focused reproduction")
    parser.add_argument("--note", default="", help="Record concurrent workloads or other measurement conditions")
    args = parser.parse_args()
    import numpy as np
    cases = json.loads(args.corpus.read_text())
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            parser.error("Unknown corpus ID")
    else:
        # Alternate categories so both models are exercised after each other.
        groups = {kind: [case for case in cases if case["category"] == kind]
                  for kind in ("short", "long", "conditional")}
        cases = [groups[kind][index] for index in range(max(map(len, groups.values())))
                 for kind in groups if index < len(groups[kind])]
    output = {"method": __doc__, "base_model": args.base_model, "complex_model": args.complex_model,
              "measurement_note": args.note,
              "corpus_sha256": hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
              "quality_review_required": True, "cases": [], "summary": {}}
    metrics = {"base_only": [], "selective": []}
    workers = {name: Translator(args.base_model, queue.Queue(), queue.Queue(),
                              performance=lambda stage, _name=name, **data:
                                  metrics[_name].append({"stage": stage, **data}),
                              complex_model=args.complex_model if name == "selective" else None)
               for name in metrics}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")

    def state():
        return [{key: model.get(key) for key in ("model", "size_vram", "context_length")}
                for model in workers["selective"]._client.ps().get("models", [])]

    def stats(values):
        return {"count": len(values), "median_seconds": float(np.median(values)) if values else None,
                "p95_seconds": float(np.percentile(values, 95)) if values else None}

    try:
        for worker in workers.values():
            worker._warmup()
        output["resident_after_warmup"] = state()
        output["warmup"] = {name: [entry for entry in log if entry["stage"] == "model_warmup"]
                            for name, log in metrics.items()}
        for index, case in enumerate(cases):
            row = {**case, "routing_reason": complexity_reason(case["source"]), "results": {}}
            # Alternate first profile too, reducing a consistent ordering bias.
            order = list(workers) if index % 2 == 0 else list(reversed(workers))
            for name in order:
                worker = workers[name]
                worker._context.clear()
                worker._context.extend(case.get("context", []))
                metrics[name].clear()
                event = SubtitleEvent("en_only", utterance_id=case["id"], revision=1, text=case["source"])
                started, translated, error = time.monotonic(), "", ""
                try:
                    translated = worker._translate(event)
                    if not translated:
                        error = "empty result"
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                row["results"][name] = {"translation": translated, "model": worker._last_model,
                                        "fallback": worker._last_fallback, "error": error,
                                        "seconds": time.monotonic() - started,
                                        "generation": list(metrics[name])}
                print(f"{case['id']} {name} {worker._last_model}: "
                      f"{row['results'][name]['seconds']:.3f}s fallback={worker._last_fallback} "
                      f"error={bool(error)}", flush=True)
            output["cases"].append(row)
            save()
        output["resident_after_comparison"] = state()
        for name in workers:
            summary = {"all": stats([row["results"][name]["seconds"] for row in output["cases"]]),
                       "categories": {kind: stats([row["results"][name]["seconds"]
                                                   for row in output["cases"] if row["category"] == kind])
                                      for kind in ("short", "long", "conditional")},
                       "fallbacks": sum(row["results"][name]["fallback"] for row in output["cases"]),
                       "request_errors": sum(bool(row["results"][name]["error"]) for row in output["cases"]),
                       "actual_models": dict(Counter(row["results"][name]["model"] for row in output["cases"]))}
            output["summary"][name] = summary
        save()
        print(json.dumps(output["summary"], ensure_ascii=False), flush=True)
    finally:
        for worker in workers.values():
            close = getattr(worker._client, "close", None) or getattr(worker._client._client, "close", None)
            if close:
                close()


if __name__ == "__main__":
    main()
