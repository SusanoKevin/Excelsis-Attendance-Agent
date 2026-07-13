from __future__ import annotations

import os
import re
import unicodedata

_MAX_MESSAGE_LEN  = int(os.getenv("MAX_MESSAGE_LEN",   "2000"))
_MAX_PROMPT_TOKENS = int(os.getenv("MAX_PROMPT_TOKENS", "2048"))

_INJECTION_PATTERNS = re.compile(
    r"ignore\s+(all\s+)?(previous|prior|your|above|the)\s+instructions?"
    r"|disregard\s+(all\s+)?(previous|prior|your|above|the)\s+instructions?"
    r"|forget\s+(all\s+)?(previous|prior|your|above|the)\s+instructions?"
    r"|you\s+are\s+now\b"
    r"|\bact\s+as\s+(a|an|if)\b"
    r"|\bpretend\s+(to\s+be|you\s+are)\b"
    r"|\broleplay\s+as\b"
    r"|\bjailbreak\b"
    r"|\bdeveloper\s+mode\b"
    r"|\bunrestricted\s+mode\b"
    r"|reveal\s+(your|the)\s+(system\s+)?prompt"
    r"|\bDAN\b",
    re.IGNORECASE,
)


# Cyrillic/Greek letters visually confusable with the Latin letters used in
# _INJECTION_PATTERNS. NFKC does not fold these — they're distinct scripts,
# not compatibility variants of the same character.
_CONFUSABLES = str.maketrans({
    "а": "a", "А": "A", "е": "e", "Е": "E", "о": "o", "О": "O",
    "р": "p", "Р": "P", "с": "c", "С": "C", "х": "x", "Х": "X",
    "і": "i", "І": "I", "у": "y", "У": "Y", "к": "k", "К": "K",
    "м": "m", "М": "M", "н": "h", "Н": "H", "т": "t", "Т": "T",
    "в": "b", "В": "B",
    "ο": "o", "Ο": "O", "ν": "v", "Ν": "N", "α": "a", "Α": "A",
})


def _normalize(text: str) -> str:
    """NFKC-normalize, fold confusables, and collapse whitespace (including zero-width/
    format chars, treated as separators rather than deleted) to defeat encoding/spacing/
    homoglyph bypass attempts."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(" " if unicodedata.category(ch) == "Cf" else ch for ch in text)
    text = text.translate(_CONFUSABLES)
    return re.sub(r"\s+", " ", text)


def validate_message(message: str) -> str:
    stripped = message.strip()
    if not stripped:
        raise ValueError("Message cannot be empty.")
    if len(stripped) > _MAX_MESSAGE_LEN:
        raise ValueError(
            f"Message too long ({len(stripped)} chars). Maximum is {_MAX_MESSAGE_LEN}."
        )
    if _INJECTION_PATTERNS.search(_normalize(stripped)):
        raise ValueError("Message contains disallowed content.")
    return stripped


def check_token_budget(message: str, history_chars: int = 0) -> None:
    estimated = (len(message) + history_chars) // 4
    if estimated > _MAX_PROMPT_TOKENS:
        raise ValueError(
            f"Input too large (~{estimated} tokens). "
            "Please shorten your message or start a new session."
        )
