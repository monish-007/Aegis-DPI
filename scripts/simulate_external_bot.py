"""External consumer-bot simulation for the Aegis-Pay A2A protocol.

Run with:
    python scripts/simulate_external_bot.py

Requires the FastAPI server to be running on localhost:8000.
"""

from __future__ import annotations

import json
import sys
import uuid

import httpx

# ── ANSI colour codes ────────────────────────────────────────────────────────
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
RESET = "\033[0m"

BASE = "http://localhost:5000"
AGENT_ID = "external_bot_alpha"


def _banner(step: int, title: str, desc: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{MAGENTA}  STEP {step}  |  {title}{RESET}")
    print(f"  {desc}")
    print(f"{CYAN}{'=' * 60}{RESET}")


def _print_result(res: httpx.Response, expected: int) -> None:
    ok = res.status_code == expected
    colour = GREEN if ok else RED
    tag = "PASS" if ok else "FAIL"
    print(
        f"  {colour}{tag}{RESET}  "
        f"HTTP {res.status_code} (expected {expected})"
    )
    try:
        body = res.json()
        print(f"  {json.dumps(body, indent=2)}")
    except Exception:
        print(f"  {YELLOW}(non-JSON response){RESET}  {res.text[:300]}")


def run() -> None:
    idempotency_key_happy = str(uuid.uuid4())

    with httpx.Client(base_url=BASE, timeout=15) as client:

        # ── Step 0: Discover catalog ─────────────────────────────────────
        _banner(0, "CATALOG DISCOVERY", "Fetching the Agent-Readable Catalog.")
        catalog_res = client.get("/v1/agent/catalog")
        print(f"  {GREEN}Catalog items:{RESET}")
        for item in catalog_res.json().get("catalog", []):
            print(
                f"    - {item['sku']:25s}  "
                f"base Rs.{item['base_price_inr']:>10,.2f}  "
                f"floor Rs.{item['minimum_profit_floor_inr']:>10,.2f}"
            )

        # ── Step 1: Happy-path purchase ──────────────────────────────────
        _banner(
            1,
            "HAPPY PATH",
                "Buy Enterprise Cloud Compute at Rs.47,000 (above its floor of Rs.45,000).",
        )
        res1 = client.post(
            "/v1/agent/checkout",
            json={
                "external_agent_id": AGENT_ID,
                "idempotency_key": idempotency_key_happy,
                "sku": "enterprise_cloud_compute",
                "negotiated_price_inr": 47000.00,
                "simulate_gateway_timeout": False,
            },
        )
        _print_result(res1, expected=201)

        # ── Step 2: Profit-floor breach ──────────────────────────────────
        _banner(
            2,
            "PROFIT-FLOOR BREACH",
                "Attempt to buy Enterprise Cloud Compute at Rs.40,000 (below floor of Rs.45,000).",
        )
        res2 = client.post(
            "/v1/agent/checkout",
            json={
                "external_agent_id": AGENT_ID,
                "idempotency_key": str(uuid.uuid4()),
                "sku": "enterprise_cloud_compute",
                "negotiated_price_inr": 40000.00,
                "simulate_gateway_timeout": False,
            },
        )
        _print_result(res2, expected=403)

        # ── Step 3: Idempotency lock ─────────────────────────────────────
        _banner(
            3,
            "IDEMPOTENCY LOCK",
            "Resend Step 1's idempotency key - expect 200, no new charge.",
        )
        res3 = client.post(
            "/v1/agent/checkout",
            json={
                "external_agent_id": AGENT_ID,
                "idempotency_key": idempotency_key_happy,
                "sku": "enterprise_cloud_compute",
                "negotiated_price_inr": 47000.00,
                "simulate_gateway_timeout": False,
            },
        )
        _print_result(res3, expected=200)

        # ── Step 4: Graceful gateway failure ─────────────────────────────
        _banner(
            4,
            "GATEWAY TIMEOUT RECOVERY",
            "Simulate a 504 from Razorpay - server must NOT crash.",
        )
        res4 = client.post(
            "/v1/agent/checkout",
            json={
                "external_agent_id": AGENT_ID,
                "idempotency_key": str(uuid.uuid4()),
                "sku": "enterprise_cloud_compute",
                "negotiated_price_inr": 47000.00,
                "simulate_gateway_timeout": True,
            },
        )
        _print_result(res4, expected=502)

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{BOLD}{GREEN}{'=' * 60}{RESET}")
    print(f"{BOLD}{GREEN}  SIMULATION COMPLETE - all 4 scenarios executed.{RESET}")
    print(f"{GREEN}{'=' * 60}{RESET}\n")


if __name__ == "__main__":
    try:
        run()
    except httpx.ConnectError:
        print(
            f"\n{RED}ERROR: Cannot reach {BASE}. "
            f"Start the server first:{RESET}\n"
            f"  uvicorn app.main:app --reload\n"
        )
        sys.exit(1)
