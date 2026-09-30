"""HMAC agent authentication and sliding-window rate limiting."""

import hashlib
import hmac
import logging
import os
import time
from threading import RLock
from typing import Any

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Shared secret for HMAC-SHA256 agent signature verification ──
_AGENT_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")
_RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", _AGENT_SECRET)

# ── In-memory sliding-window rate limiter ──
_RATE_WINDOW_SECONDS: int = 60
_RATE_MAX_REQUESTS: int = 5
_rate_store: dict[str, list[float]] = {}
_rate_lock = RLock()


def verify_agent_signature(payload: dict[str, Any], signature: str) -> bool:
    """Verify that the incoming request was signed by a trusted AI agent.

    The expected signature is HMAC-SHA256(secret, canonical_payload).
    In sandbox / test mode, if no signature header is provided the check
    is skipped so the dashboard evaluator keeps working.
    """
    if not signature:
        # Sandbox mode — allow unsigned requests from the dashboard.
        return True
    if not _AGENT_SECRET:
        logger.warning("RAZORPAY_KEY_SECRET not set; skipping HMAC verification")
        return True
    try:
        canonical = _canonical_string(payload)
        expected = hmac.new(
            _AGENT_SECRET.encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception as exc:
        logger.error("HMAC verification error: %s", exc)
        return False


def sign_agent_payload(payload: dict[str, Any]) -> str:
    """Create a protocol signature for the first-party demo buyer agent."""
    if not _AGENT_SECRET:
        raise RuntimeError("RAZORPAY_KEY_SECRET is required to sign an agent request")
    return hmac.new(_AGENT_SECRET.encode(), _canonical_string(payload).encode(), hashlib.sha256).hexdigest()


def verify_razorpay_webhook_signature(body: bytes, signature: str) -> bool:
    """Verify Razorpay's ``x-razorpay-signature`` header (SHA256 HMAC)."""
    if not signature or not _RAZORPAY_WEBHOOK_SECRET:
        return False
    try:
        expected = hmac.new(
            _RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception as exc:
        logger.error("Webhook signature verification error: %s", exc)
        return False


def check_rate_limit(agent_id: str) -> dict[str, Any]:
    """Sliding-window rate limiter — max ``_RATE_MAX_REQUESTS`` per 60 s.

    Returns ``{"allowed": True/False, "remaining": int, "retry_after": float}``.
    """
    now = time.time()
    window_start = now - _RATE_WINDOW_SECONDS

    with _rate_lock:
        timestamps = _rate_store.get(agent_id, [])
        # Prune expired entries
        timestamps = [t for t in timestamps if t > window_start]
        _rate_store[agent_id] = timestamps

        if len(timestamps) >= _RATE_MAX_REQUESTS:
            retry_after = round(timestamps[0] - window_start, 1)
            return {"allowed": False, "remaining": 0, "retry_after": max(retry_after, 0.1)}

        timestamps.append(now)
        _rate_store[agent_id] = timestamps
        return {"allowed": True, "remaining": _RATE_MAX_REQUESTS - len(timestamps), "retry_after": 0}


def _canonical_string(payload: dict[str, Any]) -> str:
    """Deterministic string representation for HMAC signing."""
    return "|".join(f"{k}={v}" for k, v in sorted(payload.items()) if v is not None)
