"""The platform's deterministic, agent-readable infrastructure priority catalog."""

from typing import Any

_CATALOG: dict[str, dict[str, Any]] = {
    "enterprise_cloud_compute": {
        "sku": "enterprise_cloud_compute",
        "name": "Healthcare · Hospital & Clinic",
        "base_price_inr": 50000.0,
        "minimum_profit_floor_inr": 45000.0,
        "bulk_floor_tiers": [{"min_quantity": 1, "floor_inr": 45000.0}, {"min_quantity": 10, "floor_inr": 43000.0}],
        "sector": "Healthcare",
        "brics_region": "India, China, Brazil",
        "demand_signals": 4217,
    },
    "api_bulk_credits": {
        "sku": "api_bulk_credits",
        "name": "Transport · Bridge & Road Repair",
        "base_price_inr": 15000.0,
        "minimum_profit_floor_inr": 12000.0,
        "bulk_floor_tiers": [{"min_quantity": 1, "floor_inr": 12000.0}, {"min_quantity": 25, "floor_inr": 11000.0}],
        "sector": "Transport",
        "brics_region": "China, Russia, India",
        "demand_signals": 3891,
    },
    "saas_annual_license": {
        "sku": "saas_annual_license",
        "name": "Education · School Construction",
        "base_price_inr": 8000.0,
        "minimum_profit_floor_inr": 7000.0,
        "bulk_floor_tiers": [{"min_quantity": 1, "floor_inr": 7000.0}, {"min_quantity": 20, "floor_inr": 6500.0}],
        "sector": "Education",
        "brics_region": "Brazil, South Africa, India",
        "demand_signals": 2614,
    },
}


def get_catalog() -> list[dict[str, Any]]:
    return list(_CATALOG.values())


def get_item(sku: str) -> dict[str, Any] | None:
    return _CATALOG.get(sku)


def get_profit_floor(item: dict[str, Any], quantity: int) -> float:
    """Select a deterministic volume tier; never delegated to an LLM."""
    eligible = [tier for tier in item.get("bulk_floor_tiers", []) if quantity >= tier["min_quantity"]]
    return float(eligible[-1]["floor_inr"] if eligible else item["minimum_profit_floor_inr"])
