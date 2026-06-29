import time
from collections.abc import Callable

from fastapi import Depends, HTTPException

from backend import auth
from backend.config import get_settings


class RateLimiter:
    """In-memory per-key token bucket. `now` is injected for deterministic tests.
    Swappable for a Redis-backed limiter for multi-node deployments."""

    def __init__(self, capacity: int, refill_per_sec: float, now: Callable[[], float]):
        self.capacity = capacity
        self.refill = refill_per_sec
        self.now = now
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def check(self, key: str) -> tuple[bool, float]:
        t = self.now()
        tokens, last = self._buckets.get(key, (float(self.capacity), t))
        tokens = min(self.capacity, tokens + (t - last) * self.refill)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, t)
            return True, 0.0
        self._buckets[key] = (tokens, t)
        retry = (1.0 - tokens) / self.refill if self.refill > 0 else 1.0
        return False, retry


_limiter: RateLimiter | None = None


def set_limiter(rl: RateLimiter | None) -> None:
    """Test seam: install a deterministic limiter, or None to rebuild from settings."""
    global _limiter
    _limiter = rl


def _get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        s = get_settings()
        _limiter = RateLimiter(s.rate_burst, s.rate_rps, time.monotonic)
    return _limiter


def enforce(principal: auth.Principal = Depends(auth.require_principal)) -> auth.Principal:
    allowed, retry = _get_limiter().check(principal.tenant)
    if not allowed:
        raise HTTPException(status_code=429, detail="rate limit exceeded",
                            headers={"Retry-After": str(int(retry) + 1)})
    return principal
