"""Repair common mojibake patterns in bookmark payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

SUSPICIOUS_CHARS = frozenset("ÃÂâÐÑ×")


def _suspicious_score(text: str) -> int:
    score = 0
    for char in text:
        codepoint = ord(char)
        if 0x80 <= codepoint <= 0x9F or char == "\ufffd":
            score += 3
        elif char in SUSPICIOUS_CHARS:
            score += 1
    return score


def _repair_once(text: str) -> str:
    original_score = _suspicious_score(text)
    if original_score == 0:
        return text

    try:
        candidate = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

    if candidate == text:
        return text
    if _suspicious_score(candidate) < original_score:
        return candidate
    return text


def repair_text(text: str) -> str:
    repaired = text
    for _ in range(2):
        candidate = _repair_once(repaired)
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


def repair_value(value):
    if isinstance(value, str):
        return repair_text(value)
    if isinstance(value, list):
        return [repair_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(repair_value(item) for item in value)
    if isinstance(value, Mapping):
        return {key: repair_value(item) for key, item in value.items()}
    return value
