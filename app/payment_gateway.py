"""Razorpay test-mode order creation."""

import os
from typing import Any

import razorpay
from dotenv import load_dotenv

load_dotenv()
# Windows environments often retain a stale localhost proxy that makes the
# Razorpay SDK fail with WinError 10061.  Payments must bypass those settings.
for _proxy_name in (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
):
    os.environ.pop(_proxy_name, None)
os.environ["NO_PROXY"] = "*"

_key_id, _secret = os.getenv("RAZORPAY_KEY_ID", ""), os.getenv("RAZORPAY_KEY_SECRET", "")
_configured = bool(_key_id and _secret and "xxxxxxxx" not in _key_id and "xxxxxxxx" not in _secret)
client: razorpay.Client | None = razorpay.Client(auth=(_key_id, _secret)) if _configured else None
if client is not None:
    client.session.trust_env = False


def execute_order(amount_inr: float, receipt: str, simulate_timeout: bool = False) -> dict[str, Any]:
    if simulate_timeout:
        raise Exception("Simulated Upstream 504 Gateway Timeout")
    paise = int(amount_inr * 100)
    if client is None:
        raise RuntimeError("Razorpay test credentials are not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET; mock orders are intentionally disabled.")
    return client.order.create({"amount": paise, "currency": "INR", "receipt": receipt})


def checkout_config(order: dict[str, Any]) -> dict[str, Any]:
    """Public values only; consumed by Razorpay Checkout in test mode."""
    if not _configured:
        return {}
    return {"key": _key_id, "order_id": order["id"], "amount": order["amount"], "currency": order["currency"], "name": "Aegis-DPI · Infrastructure Verification"}
