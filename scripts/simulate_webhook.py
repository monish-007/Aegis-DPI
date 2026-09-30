"""Deliver signed Razorpay-style webhook events to a local Aegis-Pay server.

This is a local integration verifier, not a substitute for a real Razorpay
dashboard delivery. It signs the exact raw JSON bytes with Aegis-Pay's
effective webhook secret, then sends both a valid and a deliberately invalid
signature to prove the handler's verification path.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
for _proxy_name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_proxy_name, None)
os.environ["NO_PROXY"] = "*"

DEFAULT_BASE_URL = os.getenv("AEGIS_BASE_URL", "http://127.0.0.1:8001")
ORDER_ID = os.getenv("RAZORPAY_TEST_ORDER_ID", "order_TYPAEG774CiGrz")
PAYMENT_ID = os.getenv("RAZORPAY_TEST_PAYMENT_ID", "pay_aegis_local_captured_001")


def payment_captured_payload() -> dict[str, object]:
    """A Razorpay ``payment.captured`` event envelope with a payment entity."""
    now = int(time.time())
    return {
        "entity": "event",
        "account_id": "acc_aegis_test_mode",
        "event": "payment.captured",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": PAYMENT_ID,
                    "entity": "payment",
                    "amount": 4_700_000,
                    "currency": "INR",
                    "status": "captured",
                    "order_id": ORDER_ID,
                    "invoice_id": None,
                    "international": False,
                    "method": "card",
                    "amount_refunded": 0,
                    "refund_status": None,
                    "captured": True,
                    "description": "Aegis-Pay local webhook integration verification",
                    "card_id": None,
                    "bank": None,
                    "wallet": None,
                    "vpa": None,
                    "email": "buyer@example.test",
                    "contact": "+919999999999",
                    "notes": [],
                    "fee": 0,
                    "tax": 0,
                    "error_code": None,
                    "error_description": None,
                    "created_at": now,
                }
            }
        },
        "created_at": now,
    }


def signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def persisted_status() -> str | None:
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        return None
    client = create_client(url, key)
    result = client.table("orders").select("status,razorpay_payment_id").eq("razorpay_order_id", ORDER_ID).execute()
    if not result.data:
        return None
    row = result.data[0]
    return f"{row.get('status')} (payment_id={row.get('razorpay_payment_id')})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Send signed Razorpay webhook fixtures to Aegis-Pay.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Aegis-Pay base URL (default: {DEFAULT_BASE_URL})")
    args = parser.parse_args()
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET") or os.getenv("RAZORPAY_KEY_SECRET", "")
    if not secret:
        print("ERROR: RAZORPAY_WEBHOOK_SECRET or RAZORPAY_KEY_SECRET is required.")
        return 2

    body = json.dumps(payment_captured_payload(), separators=(",", ":")).encode("utf-8")
    endpoint = f"{args.base_url.rstrip('/')}/v1/webhooks/razorpay"
    with httpx.Client(timeout=15) as client:
        valid = client.post(endpoint, content=body, headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature(body, secret)})
        invalid = client.post(endpoint, content=body, headers={"Content-Type": "application/json", "X-Razorpay-Signature": "0" * 64})

    print("valid_webhook_http=", valid.status_code)
    print("valid_webhook_body=", valid.text)
    print("supabase_order_status=", persisted_status())
    print("invalid_webhook_http=", invalid.status_code)
    print("invalid_webhook_body=", invalid.text)
    return 0 if valid.status_code == 200 and invalid.status_code == 401 else 1


if __name__ == "__main__":
    sys.exit(main())
