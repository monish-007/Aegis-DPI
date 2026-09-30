"""Zero-LLM infrastructure priority policy enforcement for citizen-originated requests."""

from typing import Any

from .schemas import A2ACheckoutRequest
from .catalog_engine import get_profit_floor


def validate_transaction(request: A2ACheckoutRequest, item: dict[str, Any]) -> dict[str, Any]:
    floor = get_profit_floor(item, request.quantity)
    if request.negotiated_price_inr < floor:
        return {"approved": False, "status_code": 403, "reason": f"POLICY_BREACH: Bid below merchant profit floor (₹{floor:,.0f} at quantity {request.quantity})", "profit_floor_inr": floor}
    if request.negotiated_price_inr > 100000.0:
        return {"approved": False, "status_code": 403, "reason": "POLICY_BREACH: Exceeds per-transaction ceiling"}
    return {"approved": True, "status_code": 200, "reason": "APPROVED", "profit_floor_inr": floor}
