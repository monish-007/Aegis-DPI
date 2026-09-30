"""Supabase-backed audit and order storage with a safe in-memory fallback."""

import logging
import os
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Optional

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()
logger = logging.getLogger(__name__)
for _proxy_name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_proxy_name, None)
os.environ["NO_PROXY"] = "*"
_url, _key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", "")
_client: Optional[Client] = create_client(_url, _key) if _url and _key else None
_memory_mode = _client is None
_orders: dict[str, dict[str, Any]] = {}
_ledger: list[dict[str, Any]] = []
_rate_breaches: list[dict[str, Any]] = []
_lock = RLock()


def _fallback(error: Exception) -> None:
    global _memory_mode
    logger.warning("Supabase unavailable; using in-memory storage: %s", error)
    _memory_mode = True


def check_and_get_order(idempotency_key: str) -> Optional[dict[str, Any]]:
    if not _memory_mode:
        try:
            result = _client.table("orders").select("*").eq("idempotency_key", idempotency_key).limit(1).execute()  # type: ignore[union-attr]
            return result.data[0] if result.data else None
        except Exception as exc:
            _fallback(exc)
    with _lock:
        return _orders.get(idempotency_key)


def save_order(order_data: dict[str, Any]) -> dict[str, Any]:
    if not _memory_mode:
        try:
            result = _client.table("orders").insert(order_data).execute()  # type: ignore[union-attr]
            return result.data[0] if result.data else order_data
        except Exception as exc:
            _fallback(exc)
    with _lock:
        _orders[order_data["idempotency_key"]] = order_data.copy()
        return _orders[order_data["idempotency_key"]]


def update_order_status(order_id: str, status: str, payment_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Update an existing order's status (e.g. CREATED → FULFILLED)."""
    if not _memory_mode:
        try:
            result = (
                _client.table("orders")  # type: ignore[union-attr]
                .update({"status": status, "razorpay_payment_id": payment_id} if payment_id else {"status": status})
                .eq("razorpay_order_id", order_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as exc:
            _fallback(exc)
    # In-memory fallback: scan for matching razorpay_order_id
    with _lock:
        for order in _orders.values():
            if order.get("razorpay_order_id") == order_id:
                order["status"] = status
                if payment_id:
                    order["razorpay_payment_id"] = payment_id
                return order
    return None


def record_audit_event(agent_id: str, event_type: str, status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    row = {"agent_id": agent_id, "event_type": event_type, "status_code": status_code, "payload": payload, "created_at": datetime.now(timezone.utc).isoformat()}
    if not _memory_mode:
        try:
            result = _client.table("audit_logs").insert(row).execute()  # type: ignore[union-attr]
            return result.data[0] if result.data else row
        except Exception as exc:
            _fallback(exc)
    with _lock:
        row["id"] = len(_ledger) + 1
        _ledger.append(row)
    return row


def get_audit_ledger(limit: int = 20) -> list[dict[str, Any]]:
    if not _memory_mode:
        try:
            result = _client.table("audit_logs").select("*").order("created_at", desc=True).limit(limit).execute()  # type: ignore[union-attr]
            return result.data or []
        except Exception as exc:
            _fallback(exc)
    with _lock:
        return list(reversed(_ledger[-limit:]))


def log_rate_limit_breach(agent_id: str) -> dict[str, Any]:
    """Record a rate-limit breach in both the audit ledger and a dedicated list."""
    row = record_audit_event(
        agent_id,
        "RATE_LIMIT_BREACH",
        429,
        {"agent_id": agent_id, "reason": "Exceeded 5 requests per 60 seconds"},
    )
    with _lock:
        _rate_breaches.append(row)
    return row
