from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator


SLOW_OPERATION_MS = 5_000.0


@contextmanager
def timing_log(logger: logging.Logger, operation: str, **context: Any) -> Iterator[None]:
    """Log START/END and elapsed time for a potentially slow operation."""
    started = time.perf_counter()
    suffix = " ".join(f"{key}={value}" for key, value in context.items() if value is not None)
    logger.info("timing operation=%s event=START%s", operation, f" {suffix}" if suffix else "")
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1_000
        log = logger.warning if elapsed_ms > SLOW_OPERATION_MS else logger.info
        log(
            "timing operation=%s event=END elapsed_ms=%.2f slow_over_5s=%s%s",
            operation,
            elapsed_ms,
            elapsed_ms > SLOW_OPERATION_MS,
            f" {suffix}" if suffix else "",
        )
