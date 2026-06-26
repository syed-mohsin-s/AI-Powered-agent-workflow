"""
Sentinel-AI API Gateway Middleware.

Two middleware layers that sit in front of all API routes:

1. **RateLimitMiddleware** — sliding-window rate limiter per client IP.
2. **JWTGatewayMiddleware** — validates Bearer tokens on protected paths.

Both middlewares are configured via ``SentinelConfig.gateway`` and gracefully
degrade in dev-mode (default JWT secret ⇒ auth enforcement is skipped).
"""

import time
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from sentinel_ai.config import get_config
from sentinel_ai.utils.logger import get_logger

logger = get_logger("api.gateway")


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------


class _SlidingWindowCounter:
    """Simple per-key sliding-window counter with auto-expiry."""

    def __init__(self):
        self._hits: dict[str, list[float]] = defaultdict(list)

    def hit(self, key: str, now: float | None = None) -> int:
        """Record a hit and return the current count within the window."""
        now = now or time.time()
        bucket = self._hits[key]
        bucket.append(now)
        return len(bucket)

    def prune(self, key: str, window_seconds: float, now: float | None = None) -> None:
        """Remove entries older than the window."""
        now = now or time.time()
        cutoff = now - window_seconds
        bucket = self._hits.get(key)
        if bucket:
            self._hits[key] = [t for t in bucket if t > cutoff]

    def count(self, key: str, window_seconds: float, now: float | None = None) -> int:
        """Return hits within the window (after pruning)."""
        self.prune(key, window_seconds, now)
        return len(self._hits.get(key, []))


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter per client IP.

    Returns ``429 Too Many Requests`` with a ``Retry-After`` header when the
    limit is exceeded.
    """

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self._counter = _SlidingWindowCounter()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        config = get_config()
        gateway = config.gateway

        # Skip exempt paths
        path = request.url.path
        if any(path.startswith(exempt) for exempt in gateway.exempt_paths):
            return await call_next(request)

        # Only rate-limit API paths
        if not path.startswith("/api"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        key = f"rl:{client_ip}"
        window = 60.0  # 1 minute

        # Prune + count
        now = time.time()
        self._counter.prune(key, window, now)
        current = self._counter.count(key, window, now)

        if current >= gateway.rate_limit_per_minute:
            retry_after = int(window - (now - self._counter._hits[key][0])) + 1
            logger.warning(
                f"Rate limit exceeded for {client_ip}: {current}/{gateway.rate_limit_per_minute}",
                extra_data={"client_ip": client_ip},
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "Rate limit exceeded",
                    "limit": gateway.rate_limit_per_minute,
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        # Record hit
        self._counter.hit(key, now)

        # Execute request
        response = await call_next(request)
        return response


# ---------------------------------------------------------------------------
# JWT Authentication Gateway
# ---------------------------------------------------------------------------


class JWTGatewayMiddleware(BaseHTTPMiddleware):
    """Validates JWT Bearer tokens on protected ``/api/*`` routes.

    In **dev-mode** (when the JWT secret is the placeholder default) this
    middleware is effectively a pass-through so that the system remains
    usable without tokens during local development.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        config = get_config()
        gateway = config.gateway
        path = request.url.path

        # Skip non-API and exempt paths
        if not path.startswith("/api"):
            return await call_next(request)

        if any(path.startswith(exempt) for exempt in gateway.exempt_paths):
            return await call_next(request)

        # Dev-mode: skip auth entirely when using the default secret
        if config.jwt.secret_key in ("", "sentinel-ai-default-secret-change-me"):
            request.state.user = {"sub": "dev-user", "scopes": ["admin"]}
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:]  # strip "Bearer "

        try:
            from sentinel_ai.api.auth import decode_token

            payload = decode_token(token)
            request.state.user = payload
        except Exception as exc:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Authentication failed: {exc}"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Authenticated — continue
        response = await call_next(request)
        return response
