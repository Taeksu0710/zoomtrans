"""Cheap, conservative text boundaries; no model calls on the STT path."""
import re

_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "etc",
                  "e.g", "i.e", "a.m", "p.m", "u.s", "u.k", "fig", "inc"}
_TRAILING = re.compile(
    r"\b(?:and|or|but|because|if|unless|although|while|when|that|which|whose|"
    r"to|of|for|with|from|into|the|a|an|my|your|our|their|is|are|was|were|"
    r"will|would|can|could|should|must|not)\W*$", re.IGNORECASE,
)
_BARE_CONDITIONAL = re.compile(r"^(?:if|unless|although|because)\b", re.IGNORECASE)


def split_sentences(text):
    """Keep decimals, dates, common abbreviations and initials together."""
    parts, start = [], 0
    for match in re.finditer(r"[.!?。！？]+[\"'’”)]*(?=\s|$)", text):
        if match.group().startswith("."):
            token = re.search(r"([\w.]+)$", text[:match.start()])
            word = token.group(1).lower() if token else ""
            if word in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)*[a-z]", word):
                continue
            if word == "no" and re.match(r"\s+\d", text[match.end():]):
                continue
            if word.isdigit() and re.match(r"\s+\d", text[match.end():]):
                continue
        part = text[start:match.end()].strip()
        if part:
            parts.append(part)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def is_incomplete(text, language="en"):
    """Delay obvious fragments briefly; this isn't a grammatical proof."""
    if language != "en":
        return False
    last = (split_sentences(text) or [text])[-1].strip()
    if _TRAILING.search(last):
        return True
    # A comma inside a long conditional is not proof that its main clause has
    # arrived. ASR also adds periods to bare dependent clauses.
    if _BARE_CONDITIONAL.match(last):
        words = last.split()
        if not re.search(
            r"\b(?:then|we|you|they|I|it)\s+(?:will|would|can|should|must)\b",
            " ".join(words[2:]), re.IGNORECASE,
        ):
            return True
    return False
