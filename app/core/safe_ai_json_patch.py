from __future__ import annotations

import json
import logging
import re
from json import JSONDecodeError
from typing import Any

logger = logging.getLogger(__name__)
_ORIGINAL_JSON_LOADS = json.loads
_PATCH_MARKER = "_SAFE_AI_JSON_PATCHED"


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_json_candidate(text: str) -> str | None:
    cleaned = _strip_code_fences(text)
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            _, end = decoder.raw_decode(cleaned[index:])
            return cleaned[index : index + end]
        except JSONDecodeError:
            continue

    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if match:
        return match.group(0).strip()
    return None


def safe_ai_json_loads(value: str | bytes | bytearray, *args: Any, **kwargs: Any) -> Any:
    try:
        return _ORIGINAL_JSON_LOADS(value, *args, **kwargs)
    except Exception as original_error:
        if not isinstance(value, str):
            raise
        candidate = _extract_json_candidate(value)
        if not candidate or candidate == value:
            raise
        try:
            parsed = _ORIGINAL_JSON_LOADS(candidate, *args, **kwargs)
            logger.warning(
                "safe_ai_json_recovered original_error=%s raw_preview=%s",
                type(original_error).__name__,
                value[:500].replace("\n", " "),
            )
            return parsed
        except Exception:
            logger.warning(
                "safe_ai_json_failed raw_preview=%s",
                value[:500].replace("\n", " "),
            )
            raise original_error


def install_safe_ai_json_patch() -> None:
    if getattr(json, _PATCH_MARKER, False):
        return
    json.loads = safe_ai_json_loads  # type: ignore[assignment]
    setattr(json, _PATCH_MARKER, True)
    logger.info("safe_ai_json_patch_installed")
