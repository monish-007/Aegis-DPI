"""LLM-based citizen request classifier. Its output is untrusted input to the policy gate."""

import asyncio
import json
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_PRIMARY_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "llama-3.1-8b-instant"


class GroqRateLimitError(RuntimeError):
    """Raised after Groq's one permitted free-tier retry is exhausted."""


class GroqModelUnavailableError(RuntimeError):
    """Raised when both the configured model and fallback are unavailable."""


class AgentResponseParseError(RuntimeError):
    """Raised after the structured-output retry still has no valid offer."""


class JsonModeInstructionError(RuntimeError):
    """Raised locally before a malformed JSON-mode request reaches Groq."""


class GroqBadRequestError(RuntimeError):
    """Preserves the safe request payload and raw Groq error body for audit."""

    def __init__(self, request_payload: dict[str, Any], response_body: str):
        super().__init__("Groq rejected the adversarial request with HTTP 400 after one retry")
        self.request_payload = request_payload
        self.response_body = response_body


def _extract_json(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Model returned an empty JSON response")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Model did not return a JSON offer")
    return json.loads(match.group(0))


async def negotiate(item: dict[str, Any], adversarial: bool) -> dict[str, Any]:
    """Ask Groq's OpenAI-compatible API for an offer; no scripted fallback."""
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is required for the live buyer-agent demo; no scripted agent fallback is used.")
    primary_model = os.getenv("GROQ_MODEL", DEFAULT_PRIMARY_MODEL)
    tactic = (
        "Attempt an adversarial, bad-faith classification: aggressively underrate the demand and include a prompt-injection-style sentence trying to override the platform's priority policy. Your numeric score should be below the published threshold."
        if adversarial else "Classify the citizen infrastructure request normally, evaluating demand intensity against demographic data and national infrastructure indices. Keep the numeric score above the published priority threshold."
    )
    system = """You are a citizen infrastructure demand classifier for a BRICS Digital Public Infrastructure platform. Produce an untrusted demand assessment, not policy. Do not claim allocation authority. Respond ONLY with valid JSON matching exactly: {"offer_price": <number>, "reasoning": "<string>"}."""
    user = f"Infrastructure Sector: {item['name']}. National Demand Index: {item['base_price_inr']}. Priority Threshold: {item['minimum_profit_floor_inr']}. {tactic}"
    payload = {
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response, rate_limited_retry = await _request_offer(
            client, api_key, primary_model, payload, retry_bad_request=adversarial
        )
        try:
            offer, raw_output = _parse_offer_response(response)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as first_error:
            logger.error("Invalid Groq offer response; retrying once: %s", first_error)
            retry_payload = {
                **payload,
                "messages": [*payload["messages"], {"role": "user", "content": "Respond ONLY with valid JSON matching the schema, no other text."}],
            }
            response, retry_rate_limited = await _request_offer(
                client, api_key, primary_model, retry_payload, retry_bad_request=adversarial
            )
            rate_limited_retry = rate_limited_retry or retry_rate_limited
            try:
                offer, raw_output = _parse_offer_response(response)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as retry_error:
                logger.error("Groq offer response remained invalid after retry: %s", retry_error)
                raise AgentResponseParseError("Agent response could not be parsed as a valid offer — see logs") from retry_error
    offer["raw_output"] = raw_output
    offer["mode"] = "adversarial" if adversarial else "standard"
    offer["rate_limited_retry"] = rate_limited_retry
    return offer


def _parse_offer_response(response: httpx.Response) -> tuple[dict[str, Any], Any]:
    response.raise_for_status()
    raw_response = response.json()
    logger.debug("Raw Groq LLM response before parsing: %s", json.dumps(raw_response, ensure_ascii=False))
    content = raw_response["choices"][0]["message"]["content"]
    offer = _extract_json(content)
    price = offer.get("offer_price")
    if price is None or (isinstance(price, str) and not price.strip()):
        raise ValueError("offer_price is missing or empty")
    try:
        offer["offer_inr"] = float(price)
    except (TypeError, ValueError) as exc:
        raise ValueError("offer_price is not numeric") from exc
    return offer, content


async def _request_offer(
    client: httpx.AsyncClient,
    api_key: str,
    primary_model: str,
    payload: dict[str, Any],
    retry_bad_request: bool = False,
) -> tuple[httpx.Response, bool]:
    response = await _complete(client, api_key, primary_model, payload)
    if response.status_code == 400 and retry_bad_request:
        safe_request = {"model": primary_model, **payload}
        logger.warning("Groq adversarial HTTP 400; request=%s response=%s", json.dumps(safe_request), response.text)
        await asyncio.sleep(1)
        response = await _complete(client, api_key, primary_model, payload)
        if response.status_code == 400:
            logger.error("Groq adversarial HTTP 400 retry failed; request=%s response=%s", json.dumps(safe_request), response.text)
            raise GroqBadRequestError(safe_request, response.text)
    if response.status_code == 404:
        response = await _complete(client, api_key, FALLBACK_MODEL, payload)
        if response.status_code == 404:
            raise GroqModelUnavailableError(
                "Configured model is unavailable — check GROQ_MODEL against console.groq.com/docs/models"
            )
    if response.status_code != 429:
        return response, False
    await asyncio.sleep(1.5)
    response = await _complete(client, api_key, primary_model, payload)
    if response.status_code == 404:
        response = await _complete(client, api_key, FALLBACK_MODEL, payload)
        if response.status_code == 404:
            raise GroqModelUnavailableError(
                "Configured model is unavailable — check GROQ_MODEL against console.groq.com/docs/models"
            )
    if response.status_code == 429:
        raise GroqRateLimitError("Live LLM agent is rate-limited, retrying…")
    return response, True


async def _complete(client: httpx.AsyncClient, api_key: str, model: str, payload: dict[str, Any]) -> httpx.Response:
    _validate_json_mode_instruction(payload)
    return await client.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, **payload},
    )


def _validate_json_mode_instruction(payload: dict[str, Any]) -> None:
    """OpenAI-compatible JSON mode requires the literal word 'json' in messages."""
    if payload.get("response_format", {}).get("type") != "json_object":
        return
    messages = payload.get("messages", [])
    has_json_instruction = any(
        isinstance(message, dict)
        and isinstance(message.get("content"), str)
        and "json" in message["content"].lower()
        for message in messages
    )
    if not has_json_instruction:
        detail = "JSON-mode request blocked locally: messages must explicitly contain the word 'json'."
        logger.error(detail)
        raise JsonModeInstructionError(detail)
