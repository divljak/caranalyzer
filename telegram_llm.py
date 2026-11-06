"""Optional Gemini interpretation for questions the deterministic parser cannot read.

The model never answers questions and never sees database content. It only turns a
chat message (plus, at most, the scope of the previous answer) into a small request
that is validated here against fixed limits and then handled by the same read-only
queries as every other question. Any failure returns ``None`` so the bot falls back to
its normal "I can't answer that" reply.

Only the user's message text leaves the server. The API key is sent in a header, never
in a URL, and no error text, message text or key is ever logged.
"""

from dataclasses import dataclass
import json
import logging
import os
import re

import requests

from telegram_intent import (
    Filters,
    Intent,
    MAX_QUESTION,
    MILEAGE_RANGE,
    PRICE_RANGE,
    YEAR_RANGE,
    clean_search,
)


LOG = logging.getLogger("telegram_llm")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
TIMEOUT_SECONDS = 8
MAX_REPLY_CHARS = 4000
INTENTS = ("status", "latest", "trends", "find", "model_stats", "compare", "clarify", "chitchat",
           "unsupported")

SYSTEM_PROMPT = """You read chat messages sent to a private bot that answers questions about used-car \
listings in Bosnia (olx.ba). Messages are English or Bosnian. You do NOT answer questions and you \
know no prices or listings. You only translate the message into one JSON request.

Choose intent:
- find: the user wants listings. Set search (make/model words only, e.g. "golf", "skoda octavia"), \
and any of budget_km (max price in KM), year_min, mileage_max (km).
- model_stats: asks what a model typically costs. Set search.
- compare: compares exactly two models. Set compare_a and compare_b.
- trends: asks how asking prices changed. Set days (1-90).
- status: asks whether data is fresh or the collector works.
- latest: asks what is new.
- clarify: wants a recommendation or is too vague to search. Put ONE short friendly question in \
clarifying_question asking for a budget, model, year or mileage. No numbers, no facts.
- chitchat: greeting or thanks or small talk.
- unsupported: anything else, including forecasts and non-car topics.
Prices are in KM. If a previous scope is provided and the message only refines it (for example \
"cheaper", "newer", "what about passat"), set refines_previous to true and fill only what changed. \
Leave unused fields empty or null. Output JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "search": {"type": "string"},
        "budget_km": {"type": "integer"},
        "year_min": {"type": "integer"},
        "mileage_max": {"type": "integer"},
        "days": {"type": "integer"},
        "compare_a": {"type": "string"},
        "compare_b": {"type": "string"},
        "refines_previous": {"type": "boolean"},
        "clarifying_question": {"type": "string"},
    },
    "required": ["intent"],
}


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls, environ=None):
        values = os.environ if environ is None else environ
        key = (values.get("GEMINI_API_KEY") or "").strip()
        if not key:
            return None
        model = (values.get("TELEGRAM_LLM_MODEL") or "").strip() or DEFAULT_MODEL
        if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]{2,63}", model):
            return None
        return cls(api_key=key, model=model)


def _bounded(value, bounds):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = int(value)
    return value if bounds[0] <= value <= bounds[1] else None


def _safe_question(value):
    value = re.sub(r"\s+", " ", value or "").strip()
    if not value or len(value) > 200 or re.search(r"[0-9@/\\<>]|https?:", value):
        return ""
    return value


def intent_from_plan(plan, previous=None):
    """Validate a model-produced plan and convert it to an Intent, or None."""
    if not isinstance(plan, dict):
        return None
    kind = plan.get("intent")
    if kind not in INTENTS:
        return None
    if kind in {"status", "latest"}:
        return Intent(kind)
    if kind == "chitchat":
        return Intent("chitchat")
    if kind == "unsupported":
        return Intent("unsupported")
    if kind == "trends":
        days = _bounded(plan.get("days"), (1, 90)) or 7
        return Intent("trends", days=days)
    if kind == "clarify":
        return Intent("clarify", question=_safe_question(plan.get("clarifying_question")))
    if kind == "compare":
        first = clean_search(str(plan.get("compare_a") or ""))
        second = clean_search(str(plan.get("compare_b") or ""))
        if first and second and first.lower() != second.lower():
            return Intent("compare", labels=(first, second))
        return Intent("clarify")
    search = clean_search(str(plan.get("search") or ""))
    if kind == "model_stats":
        return Intent("model_stats", Filters(search)) if search else Intent("clarify")
    filters = Filters(
        search=search,
        budget=_bounded(plan.get("budget_km"), PRICE_RANGE),
        year_min=_bounded(plan.get("year_min"), YEAR_RANGE),
        mileage_max=_bounded(plan.get("mileage_max"), MILEAGE_RANGE),
    )
    if plan.get("refines_previous") is True and previous is not None and previous.kind == "find":
        base = previous.filters
        filters = Filters(
            search=filters.search or base.search,
            budget=filters.budget or base.budget,
            year_min=filters.year_min or base.year_min,
            mileage_max=filters.mileage_max or base.mileage_max,
        )
    if filters.is_empty():
        return Intent("clarify")
    return Intent("find", filters)


def _previous_scope(previous):
    if previous is None:
        return None
    if previous.kind == "find":
        f = previous.filters
        return {"intent": "find", "search": f.search, "budget_km": f.budget,
                "year_min": f.year_min, "mileage_max": f.mileage_max}
    if previous.kind == "trends":
        return {"intent": "trends", "days": previous.days}
    return None


def _request_body(message, previous, level):
    user = {"message": message[:MAX_QUESTION]}
    scope = _previous_scope(previous)
    if scope:
        user["previous_scope"] = scope
    config = {"responseMimeType": "application/json", "maxOutputTokens": 400}
    if level == 0:
        config["responseJsonSchema"] = SCHEMA
        config["thinkingConfig"] = {"thinkingLevel": "MINIMAL"}
    elif level == 1:
        config["responseJsonSchema"] = SCHEMA
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(user, ensure_ascii=False)}]}],
        "generationConfig": config,
    }


def _plan_text(data):
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not candidates or not isinstance(candidates[0], dict):
        return None
    candidate = candidates[0]
    if candidate.get("finishReason") not in (None, "STOP"):
        return None
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    return text.strip() or None


def _parse_plan(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except ValueError:
        return None


def _key_rejected(response):
    """Gemini reports a bad key as HTTP 400 with reason API_KEY_INVALID (or a 'key' message)."""
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return False
    reasons = [d.get("reason") for d in error.get("details") or [] if isinstance(d, dict)]
    return "API_KEY_INVALID" in reasons or "api key not valid" in str(error.get("message", "")).lower()


def call_plan(message, previous, config, *, post=None):
    """Return (plan dict | None, status string). Never raises; status has no secrets."""
    post = post or requests.post
    last = "failed"
    for level in (0, 1, 2):
        try:
            response = post(
                GEMINI_URL.format(model=config.model),
                headers={"x-goog-api-key": config.api_key, "content-type": "application/json"},
                json=_request_body(message, previous, level),
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            return None, "network error or timeout"
        code = response.status_code
        if code == 429:
            return None, "rate limit or free quota reached (HTTP 429)"
        if code in (401, 403):
            return None, f"key rejected (HTTP {code})"
        if code == 404:
            return None, "model name not found (HTTP 404)"
        if code == 400:
            if _key_rejected(response):
                return None, "key rejected (HTTP 400, API key not valid)"
            last = "request rejected (HTTP 400)"
            continue  # retry with a simpler request shape
        if code != 200:
            return None, f"Gemini error (HTTP {code})"
        try:
            text = _plan_text(response.json())
        except ValueError:
            return None, "unreadable response"
        if not text:
            return None, "empty or blocked response"
        plan = _parse_plan(text)
        if plan is None:
            return None, "response was not valid JSON"
        return plan, f"ok (request shape {level})"
    return None, last


def interpret(message, previous, config, *, post=None):
    """Interpret one message. Returns an Intent or None; never raises."""
    if config is None or not isinstance(message, str) or not message.strip():
        return None
    try:
        plan, status = call_plan(message, previous, config, post=post)
        if plan is None:
            LOG.warning("gemini interpretation unavailable: %s", status)
            return None
        return intent_from_plan(plan, previous)
    except Exception:  # fail closed to the deterministic reply; log no content
        LOG.warning("gemini interpretation failed unexpectedly")
        return None


def check_message(config, *, post=None):
    """Human-readable health check for the /llm command. Contains no secrets."""
    if config is None:
        return "Gemini is not configured (no valid GEMINI_API_KEY / model). Plain questions use the built-in parser only."
    plan, status = call_plan("golf under 15000 KM", None, config, post=post)
    intent = intent_from_plan(plan, None) if plan else None
    if intent is None:
        return f"Gemini check failed: {status}.\nModel: {config.model}"
    return f"Gemini check: {status}.\nModel: {config.model}\nTest question understood as: {intent.kind}"
