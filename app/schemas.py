"""Request and response models for the Aegis-DPI citizen infrastructure platform."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class A2ACheckoutRequest(BaseModel):
    external_agent_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)
    sku: str = Field(min_length=1, max_length=128)
    negotiated_price_inr: float = Field(gt=0, le=1_000_000)
    quantity: int = Field(default=1, ge=1, le=10_000)
    simulate_gateway_timeout: bool = False


class CatalogItem(BaseModel):
    sku: str
    name: str
    base_price_inr: float
    minimum_profit_floor_inr: float


class AuditLogEntry(BaseModel):
    id: Optional[int] = None
    agent_id: str
    event_type: str
    status_code: int
    payload: dict[str, Any] = Field(default_factory=dict)


class RazorpayWebhookPayload(BaseModel):
    """Simplified model for Razorpay webhook events.

    Razorpay sends an event envelope with order and payment entities.
    We accept any shape and extract the order ID safely.
    """
    event: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    def get_order_id(self) -> Optional[str]:
        """Extract ``payload.order.entity.id`` safely."""
        try:
            return self.payload["order"]["entity"]["id"]
        except (KeyError, TypeError):
            try:
                return self.payload["payment"]["entity"]["order_id"]
            except (KeyError, TypeError):
                return None

    def get_payment_id(self) -> Optional[str]:
        """Extract the payment identifier from Razorpay's webhook envelope."""
        try:
            return self.payload["payment"]["entity"]["id"]
        except (KeyError, TypeError):
            return None
