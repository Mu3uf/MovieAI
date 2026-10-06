"""Tiny request timer: logs start/end of each step and keeps totals for the response."""
import logging
import time
from contextlib import contextmanager

log = logging.getLogger("timing")


class Timings:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.steps: dict[str, float] = {}

    @contextmanager
    def step(self, name: str):
        start = time.perf_counter()
        log.info("%s start", name)
        try:
            yield
        finally:
            ms = (time.perf_counter() - start) * 1000
            self.steps[name] = round(self.steps.get(name, 0) + ms, 1)
            log.info("%s end (%.0f ms)", name, ms)

    def summary(self) -> dict:
        total = round((time.perf_counter() - self.t0) * 1000, 1)
        log.info("request total %.0f ms | %s", total, self.steps)
        return {"total_ms": total, **self.steps}
