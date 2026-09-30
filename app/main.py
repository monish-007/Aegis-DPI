"""Aegis-DPI: a citizen-centric infrastructure prioritization platform."""

from pathlib import Path
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .bounding_gate import validate_transaction
from .catalog_engine import get_catalog, get_item
from .buyer_agent import AgentResponseParseError, GroqBadRequestError, GroqModelUnavailableError, GroqRateLimitError, negotiate
from .database import (
    check_and_get_order,
    get_audit_ledger,
    log_rate_limit_breach,
    record_audit_event,
    save_order,
    update_order_status,
)
from .payment_gateway import checkout_config, execute_order
from .schemas import A2ACheckoutRequest, RazorpayWebhookPayload
from .security import check_rate_limit, sign_agent_payload, verify_agent_signature, verify_razorpay_webhook_signature

app = FastAPI(title="Aegis-DPI", version="1.0.0", description="Citizen-centric AI platform for infrastructure prioritization")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "aegis-dpi"}


@app.get("/v1/agent/catalog")
def catalog() -> dict[str, Any]:
    return {"catalog": get_catalog()}


@app.get("/v1/agent/audit")
def audit() -> dict[str, Any]:
    return {"audit_logs": get_audit_ledger()}


@app.post("/v1/agent/checkout", status_code=201)
def checkout(
    request: A2ACheckoutRequest,
    x_agent_signature: Optional[str] = Header(None),
) -> JSONResponse:
    # ── 1. HMAC Agent Authentication ──
    if not verify_agent_signature(request.model_dump(), x_agent_signature or ""):
        record_audit_event(request.external_agent_id, "AUTH_FAILURE", 401, {"reason": "Invalid HMAC signature"})
        return JSONResponse(status_code=401, content={"status": "UNAUTHORIZED", "detail": "Invalid agent signature"})

    # ── 2. Rate Limiting ──
    rate = check_rate_limit(request.external_agent_id)
    if not rate["allowed"]:
        log_rate_limit_breach(request.external_agent_id)
        return JSONResponse(
            status_code=429,
            content={
                "status": "RATE_LIMITED",
                "detail": f"Exceeded 5 requests per 60s for agent '{request.external_agent_id}'",
                "retry_after": rate["retry_after"],
            },
            headers={"Retry-After": str(int(rate["retry_after"]))},
        )

    # ── 3. Idempotency Check ──
    existing = check_and_get_order(request.idempotency_key)
    if existing:
        record_audit_event(request.external_agent_id, "IDEMPOTENT_CACHE_HIT", 200, {"idempotency_key": request.idempotency_key})
        return JSONResponse(status_code=200, content={"status": "IDEMPOTENT_CACHE_HIT", "order": existing})

    # ── 4. SKU Lookup ──
    item = get_item(request.sku)
    if item is None:
        record_audit_event(request.external_agent_id, "SKU_NOT_FOUND", 404, {"sku": request.sku})
        raise HTTPException(status_code=404, detail=f"SKU '{request.sku}' not found")

    # ── 5. Bounding Gate ──
    decision = validate_transaction(request, item)
    if not decision["approved"]:
        record_audit_event(request.external_agent_id, "POLICY_VIOLATION", 403, {"sku": request.sku, "reason": decision["reason"], "idempotency_key": request.idempotency_key})
        return JSONResponse(status_code=403, content={"status": "REJECTED", "detail": decision["reason"]})

    # ── 6. Payment Execution ──
    try:
        razorpay_order = execute_order(request.negotiated_price_inr, request.idempotency_key, request.simulate_gateway_timeout)
    except Exception as exc:
        record_audit_event(request.external_agent_id, "TIMEOUT_INTERCEPTED", 502, {"error": str(exc), "idempotency_key": request.idempotency_key})
        return JSONResponse(status_code=502, content={"status": "FAILED_HANDLED_GRACEFULLY", "detail": str(exc), "retry_allowed": True})

    order = save_order({
        "idempotency_key": request.idempotency_key,
        # Legacy production schema compatibility; mirrors the canonical fields below.
        "receipt_id": request.idempotency_key,
        "external_agent_id": request.external_agent_id,
        "sku": request.sku,
        "amount_inr": request.negotiated_price_inr,
        "negotiated_price_inr": request.negotiated_price_inr,
        "razorpay_order_id": razorpay_order.get("id"),
        "status": "CREATED",
    })
    record_audit_event(request.external_agent_id, "ORDER_CREATED_BOUNDED", 201, {"sku": request.sku, "idempotency_key": request.idempotency_key, "razorpay_order_id": razorpay_order.get("id")})
    return JSONResponse(status_code=201, content={"status": "ORDER_CREATED_BOUNDED", "order": order, "razorpay": razorpay_order, "checkout": checkout_config(razorpay_order), "gate": decision})


@app.post("/v1/demo/buyer-agent/{sku}")
async def run_buyer_agent(sku: str, adversarial: bool = False) -> JSONResponse:
    """Run a real LLM classification then submit its signed assessment through the policy gate."""
    item = get_item(sku)
    if item is None:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found")
    try:
        proposal = await negotiate(item, adversarial)
        payload = A2ACheckoutRequest(
            external_agent_id="aegis_live_buyer_agent",
            idempotency_key=uuid.uuid4().hex,
            sku=sku,
            negotiated_price_inr=proposal["offer_inr"],
        )
        result = checkout(payload, sign_agent_payload(payload.model_dump()))
        body = json_response_body(result)
        record_audit_event(payload.external_agent_id, "LLM_NEGOTIATION_SUBMITTED", result.status_code, {"mode": proposal["mode"], "offer_inr": proposal["offer_inr"], "idempotency_key": payload.idempotency_key})
        return JSONResponse(status_code=result.status_code, content={"negotiation": proposal, "checkout": body})
    except GroqRateLimitError as exc:
        return JSONResponse(status_code=429, content={"status": "AGENT_RATE_LIMITED", "detail": str(exc)})
    except GroqModelUnavailableError as exc:
        return JSONResponse(status_code=503, content={"status": "AGENT_MODEL_UNAVAILABLE", "detail": str(exc)})
    except GroqBadRequestError as exc:
        record_audit_event(
            "aegis_live_buyer_agent",
            "LLM_GROQ_BAD_REQUEST",
            400,
            {"request": exc.request_payload, "response": exc.response_body},
        )
        return JSONResponse(status_code=502, content={"status": "AGENT_PROVIDER_BAD_REQUEST", "detail": "Groq rejected the adversarial request after one retry — see logs"})
    except AgentResponseParseError as exc:
        record_audit_event("aegis_live_buyer_agent", "LLM_RESPONSE_PARSE_FAILURE", 422, {"detail": str(exc)})
        return JSONResponse(status_code=422, content={"status": "AGENT_RESPONSE_INVALID", "detail": str(exc)})
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"status": "AGENT_UNAVAILABLE", "detail": str(exc)})
    except Exception as exc:
        record_audit_event("aegis_live_buyer_agent", "LLM_NEGOTIATION_FAILURE", 502, {"error": str(exc)})
        return JSONResponse(status_code=502, content={"status": "AGENT_FAILED", "detail": str(exc)})


def json_response_body(response: JSONResponse) -> dict[str, Any]:
    import json
    return json.loads(response.body)


@app.post("/v1/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: Optional[str] = Header(None),
) -> JSONResponse:
    """Async webhook listener for Razorpay payment lifecycle events.

    Flow: verify signature → update order status → log audit event.
    """
    raw_body = await request.body()

    # ── Signature Verification ──
    if not verify_razorpay_webhook_signature(raw_body, x_razorpay_signature or ""):
        record_audit_event("razorpay_webhook", "WEBHOOK_AUTH_FAILURE", 401, {"reason": "Invalid x-razorpay-signature"})
        return JSONResponse(status_code=401, content={"status": "UNAUTHORIZED", "detail": "Invalid webhook signature"})

    try:
        payload = RazorpayWebhookPayload.model_validate_json(raw_body)
    except Exception as exc:
        record_audit_event("razorpay_webhook", "WEBHOOK_PARSE_ERROR", 400, {"error": str(exc)})
        return JSONResponse(status_code=400, content={"status": "BAD_REQUEST", "detail": str(exc)})

    if payload.event not in {"payment.captured", "payment.failed", "order.paid"}:
        return JSONResponse(status_code=200, content={"status": "IGNORED", "event": payload.event})

    order_id = payload.get_order_id()
    if not order_id:
        return JSONResponse(status_code=200, content={"status": "IGNORED", "detail": "No order ID in payload"})

    # ── Update order status → FULFILLED ──
    payment_id = payload.get_payment_id()
    status = "FULFILLED" if payload.event in {"payment.captured", "order.paid"} else "PAYMENT_FAILED"
    updated = update_order_status(order_id, status, payment_id)
    record_audit_event(
        "razorpay_webhook",
        "ASYNC_PAYMENT_CAPTURED" if status == "FULFILLED" else "ASYNC_PAYMENT_FAILED",
        200,
        {"razorpay_order_id": order_id, "razorpay_payment_id": payment_id, "event": payload.event, "reconciled": updated is not None},
    )
    return JSONResponse(status_code=200, content={"status": status, "order_id": order_id, "payment_id": payment_id, "reconciled": updated is not None})
