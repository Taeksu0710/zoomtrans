"""UI-independent subtitle state, including a frozen presentation snapshot."""

import time
from messages import RuntimeStatus

MAX_HISTORY = 100


class SubtitleStore:
    def __init__(self):
        self.pairs = []
        self._retired_ids = set()
        self._snapshot = None
        self._known_at_freeze = set()
        self._unseen_ids = set()
        self.runtime = {}
        self.input_level = 0.0
        self.input_updated_at = 0.0
        self.warning = ""
        self.warning_until = 0.0

    @property
    def frozen(self):
        return self._snapshot is not None

    @property
    def unseen_count(self):
        return len(self._unseen_ids)

    @property
    def visible_pairs(self):
        return self._snapshot if self.frozen else self.pairs

    def freeze(self, enabled):
        if enabled and not self.frozen:
            self._snapshot = [dict(pair) for pair in self.pairs]
            self._known_at_freeze = self._retired_ids | {p["id"] for p in self.pairs}
        elif not enabled:
            self._snapshot = None
            self._known_at_freeze.clear()
            self._unseen_ids.clear()

    def apply(self, event):
        if isinstance(event, RuntimeStatus):
            if event.component == "input" and event.state == "level":
                self.input_level = max(0.0, min(1.0, event.level))
                self.input_updated_at = time.monotonic()
            else:
                self.runtime[event.component] = event
            return False
        if event.kind == "status":
            self.warning = event.text
            self.warning_until = time.monotonic() + 8
            return False
        if event.utterance_id in self._retired_ids:
            return False
        pair = next((p for p in self.pairs if p["id"] == event.utterance_id), None)
        if pair and event.revision < pair["revision"]:
            return False
        if event.kind == "discard":
            self._retired_ids.add(event.utterance_id)
            self._unseen_ids.discard(event.utterance_id)
            if pair:
                self.pairs.remove(pair)
                return True
            return False
        if event.kind not in {"en_only", "native", "ko_partial", "en_ko", "en_skip", "en_error"}:
            return False
        if pair is None:
            pair = {"id": event.utterance_id, "revision": -1}
            self.pairs.append(pair)
            self._retired_ids.update(p["id"] for p in self.pairs[:-MAX_HISTORY])
            self.pairs = self.pairs[-MAX_HISTORY:]
            if self.frozen and event.utterance_id not in self._known_at_freeze:
                self._unseen_ids.add(event.utterance_id)
        if event.revision > pair["revision"]:
            pair.update(
                revision=event.revision, en=event.text, ko=None,
                native=False, streaming=False, error="", state="pending",
                timestamp=event.timestamp, final=event.final,
            )
        if event.kind == "native":
            pair.update(en=None, ko=event.text, native=True, streaming=False, error="", state="done")
        elif event.kind == "en_only":
            pair["en"] = event.text
        elif event.kind == "ko_partial":
            if pair["state"] != "pending":
                return False
            pair.update(ko=event.translation, streaming=True)
        elif event.kind == "en_ko":
            pair.update(ko=event.translation, streaming=False, error="", state="done")
        elif pair["state"] != "done":
            pair.update(streaming=False, state="error" if event.kind == "en_error" else "skipped")
            if event.kind == "en_error":
                pair["error"] = event.error
            else:
                pair["ko"] = ""
        return True

    def active_input_level(self, now=None):
        now = time.monotonic() if now is None else now
        return self.input_level if now - self.input_updated_at < 1.5 else 0.0
