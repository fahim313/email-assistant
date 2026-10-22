import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.exceptions.error import RateLimitError


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int, scope: str) -> None:
        self.limit: int = limit
        self.window: int = window_seconds
        self.scope: str = scope
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    async def __call__(self, request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        key = f"{self.scope}:{client}"
        now = time.monotonic()
        hits = self._hits[key]

        while hits and now - hits[0] >= self.window:
            _ = hits.popleft()

        if len(hits) >= self.limit:
            retry_after = max(1, int(self.window - (now - hits[0])) + 1)
            raise RateLimitError(retry_after)

        hits.append(now)

        # Drop idle keys so the dict can't grow without bound.
        if len(self._hits) > 10_000:
            for stale in [k for k, v in self._hits.items() if not v]:
                _ = self._hits.pop(stale, None)

    def reset(self) -> None:
        self._hits.clear()
