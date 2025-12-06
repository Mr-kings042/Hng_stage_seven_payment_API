import os
import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

from logger import get_logger


logger = get_logger(__name__)


async def add_request_id_and_process_time(request: Request, call_next):
    # Generate unique request ID
    request_id = str(uuid.uuid4()).replace('-', '')[:10]

    # Add request ID to request state for access in route handlers
    request.state.request_id = request_id

    # Log incoming request with ID
    logger.info(
        f"Request {request_id}: {request.method} {request.url.path} "
        f"from {request.client.host if request.client else 'unknown'}"
    )

    # Track processing time
    start_time = time.perf_counter()

    try:
        response = await call_next(request)
        process_time = time.perf_counter() - start_time

        # Add headers to response
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = str(process_time)

        # Log successful response
        logger.info(
            f"Request {request_id}: Completed {response.status_code} "
            f"in {process_time:.4f}s"
        )

        return response

    except Exception as e:
        process_time = time.perf_counter() - start_time

        # Log error with request ID
        logger.error(
            f"Request {request_id}: Error after {process_time:.4f}s - {str(e)}"
        )

        # Re-raise the exception to let FastAPI handle it
        raise


def rate_limit_middleware(max_requests: int = 100, window_seconds: int = 60):
    bucket: Dict[str, Deque[float]] = defaultdict(deque)

    async def _middleware(request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        key = f"{client_ip}:{path}"
        now = time.time()

        dq = bucket[key]
        # Drop timestamps outside window
        while dq and dq[0] <= now - window_seconds:
            dq.popleft()

        if len(dq) >= max_requests:
            logger.warning("Rate limit exceeded for %s", key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again later.",
            )

        dq.append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(max_requests - len(dq))
        response.headers["X-RateLimit-Window-Seconds"] = str(window_seconds)
        return response

    return _middleware


def build_rate_limiter_from_env():
    max_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "100"))
    window_seconds = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    return rate_limit_middleware(max_requests=max_requests, window_seconds=window_seconds)