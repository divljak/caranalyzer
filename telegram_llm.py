"""Optional Gemini layer: understands loose messages and phrases replies like a chat.

Gemini never decides what is true. It does two jobs:

1. ``interpret``: turn a chat message (plus recent turns and the previous answer's scope)
   into a small request that is validated here against fixed limits and then handled by the
   same read-only database queries as every other question. For small talk or a vague
   question it may also write a short reply, which is only accepted if it carries no facts
   (no numbers that were not already in the conversation, no links).
2. ``phrase``: rewrite the database's own answer as a few natural sentences. A reply is
   accepted only if every number in it already appears in the database answer, the user's
   message or the recent turns, and it contains no links; otherwise the caller sends the
   plain database answer instead.

Any failure returns ``None`` so the bot falls back to its normal replies. Only chat text and
the database answer text leave the server. The API key is sent in a header, never in a URL,
and no error text, message text or key is ever logged.
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
MAX_REPLY_CHARS = 600
MAX_FACTS_CHARS = 3500
MAX_HISTORY_TURNS = 6
MAX_TURN_CHARS = 300
LANGUAGE_NAMES = {"bs": "Bosnian", "en": "English"}
INTENTS = ("status", "latest", "trends", "find", "model_stats", "compare", "suggest", "follow", "following",
           "clarify", "chitchat", "unsupported")

PERSONA = """You are Garaža, a friendly, relaxed assistant in a private chat about used cars on \
olx.ba (Bosnia). Reply in the language named in the "language" field (Bosnian or English), warmly and briefly, \
like a person texting. You know NOTHING about cars, prices or listings yourself: the only facts you \
may use are the ones given to you from our database. Never state a price, count, year, mileage, \
date or any car fact (reliability, common faults, value, forecasts) that was not given to you, and \
never recommend a specific car from your own knowledge or call any car "the best" or "the smartest" \
choice: you may only point out what the database facts show."""

SYSTEM_PROMPT = PERSONA + """

Your job now: turn the user's latest message into ONE JSON request. You do not answer questions.

Choose intent:
- find: the user wants listings. Set search (make/model words only, e.g. "golf", "skoda octavia"), \
and any of budget_km (max price in KM), year_min, mileage_max (km).
- model_stats: asks what a model typically costs. Set search.
- compare: compares exactly two models. Set compare_a and compare_b.
- trends: asks how asking prices changed. Set days (1-90).
- status: asks whether data is fresh or the collector works.
- latest: asks what is new.
- suggest: the user gives a budget and asks what to buy, what is best, smartest or best selling, or \
for suggestions. Set budget_km (and year_min / mileage_max if given). Without a budget use clarify.
- follow: the user wants to follow or track a model. Set search (make/model words).
- following: asks what they follow, or what is new on the models they follow.
- clarify: wants a recommendation or is too vague to search. Put ONE short friendly question in \
reply. Ask for the maximum budget in KM first. Only if the message or the previous scope already \
gives a budget, ask for a model, year or mileage instead.
- chitchat: greeting, thanks, small talk or a question about you. Put a short friendly reply in \
reply and, where natural, ask what car they are looking for.
- unsupported: anything else, including forecasts and non-car topics. Put a short, kind reply in \
reply saying you only know what the database shows, and offer what you can look up.
For clarify, chitchat and unsupported the reply must contain no numbers unless the user wrote them, \
and no links. Prices are in KM. recent_turns (if any) is the conversation so far. If a previous \
scope is given and the message only refines it (for example "cheaper", "newer", "what about \
passat", "show me"), set refines_previous to true and fill only what changed. Leave unused fields \
empty or null. Output JSON only."""

PHRASE_PROMPT = PERSONA + """

Your job now: answer the user's latest message in 1-4 short sentences using ONLY the database \
answer in "facts". Quote numbers exactly as they appear in the facts; do not calculate new ones, \
round them, or add any. Do not include links. If the facts say there are no matches, that the data \
is stale, partial or a thin sample, say so plainly. You may say what the data shows, but do not \
recommend a car as best, predict prices, or add general car knowledge. If the facts list cars, \
mention only the most useful points (how many, the typical range, what stands out) and set \
show_details to true when the user asked to see, find or list cars (or "show me"), so the listings \
are attached; set it to false for a pure conversational question where the summary is enough, and \
then end by offering to show the listings. recent_turns (if any) is the conversation so far. Output JSON only."""

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
        "reply": {"type": "string"},
        "clarifying_question": {"type": "string"},
    },
    "required": ["intent"],
}

SUGGEST_ADDENDUM = """

This answer is a data-based shortlist for the user's budget. You may point out what stands out \
in it: which models have the most listings and roughly what they cost, and any listings priced well \
below similar ones, always as "what the data shows" and never as a guarantee or the best choice. \
Say plainly that we cannot see what actually sells: most listed is not best selling. Remind them \
to check each ad. Set show_details to true."""

PHRASE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "show_details": {"type": "boolean"},
    },
    "required": ["reply", "show_details"],
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


def _numbers(text):
    """Normalised digit groups in a text ('15.000' and '15,000' both become '15000')."""
    found = set()
    for raw in re.findall(r"\d[\d.,]*\d|\d", text or ""):
        digits = re.sub(r"\D", "", raw).lstrip("0") or "0"
        found.add(digits)
    return found


def _safe_text(value, allowed_numbers, limit=MAX_REPLY_CHARS):
    """A model-written sentence is accepted only if it carries no new facts or links."""
    if not isinstance(value, str):
        return ""
    value = re.sub(r"[ \t]+", " ", value).strip()
    if not value or len(value) > limit:
        return ""
    if re.search(r"@|<|>|https?:|www\.|olx\.ba|\.com|\.ba\b|`", value, re.I):
        return ""
    if not _numbers(value) <= allowed_numbers:
        return ""
    return value


def _safe_question(value):
    value = re.sub(r"\s+", " ", value or "").strip()
    if not value or len(value) > 200 or re.search(r"[0-9@/\\<>]|https?:", value):
        return ""
    return value


def _history_numbers(history):
    found = set()
    for turn in history or []:
        found |= _numbers(turn.get("text", ""))
    return found


def intent_from_plan(plan, previous=None, message="", history=None):
    """Validate a model-produced plan and convert it to an Intent, or None."""
    if not isinstance(plan, dict):
        return None
    kind = plan.get("intent")
    if kind not in INTENTS:
        return None
    allowed = _numbers(message) | _history_numbers(history)
    reply = _safe_text(plan.get("reply"), allowed)
    if kind in {"status", "latest"}:
        return Intent(kind)
    if kind == "chitchat":
        return Intent("chitchat", reply=reply)
    if kind == "unsupported":
        return Intent("unsupported", reply=reply)
    if kind == "trends":
        days = _bounded(plan.get("days"), (1, 90)) or 7
        return Intent("trends", days=days)
    if kind == "clarify":
        return Intent("clarify", reply=reply, question=_safe_question(plan.get("clarifying_question")))
    if kind == "compare":
        first = clean_search(str(plan.get("compare_a") or ""))
        second = clean_search(str(plan.get("compare_b") or ""))
        if first and second and first.lower() != second.lower():
            return Intent("compare", labels=(first, second))
        return Intent("clarify")
    search = clean_search(str(plan.get("search") or ""))
    if kind == "model_stats":
        return Intent("model_stats", Filters(search)) if search else Intent("clarify")
    if kind == "follow":
        return Intent("follow", Filters(search)) if search else Intent("clarify", reply=reply)
    if kind == "following":
        return Intent("following")
    if kind == "suggest":
        budget = _bounded(plan.get("budget_km"), PRICE_RANGE)
        if budget is None:
            return Intent("clarify", reply=reply)
        return Intent("suggest", Filters(
            budget=budget,
            year_min=_bounded(plan.get("year_min"), YEAR_RANGE),
            mileage_max=_bounded(plan.get("mileage_max"), MILEAGE_RANGE)))
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


def _clean_history(history):
    turns = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = "assistant" if turn.get("role") in {"bot", "assistant"} else "user"
        text = re.sub(r"\s+", " ", str(turn.get("text", ""))).strip()[:MAX_TURN_CHARS]
        if text:
            turns.append({"role": role, "text": text})
    return turns


def _body(system, user, schema, max_tokens, level):
    config = {"responseMimeType": "application/json", "maxOutputTokens": max_tokens}
    if level == 0:
        config["responseJsonSchema"] = schema
        config["thinkingConfig"] = {"thinkingLevel": "MINIMAL"}
    elif level == 1:
        config["responseJsonSchema"] = schema
    return {
        "systemInstruction": {"parts": [{"text": system}]},
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


def _generate(config, system, user, schema, max_tokens, post=None):
    """Return (parsed JSON | None, status string). Never raises; status has no secrets."""
    post = post or requests.post
    last = "failed"
    for level in (0, 1, 2):
        try:
            response = post(
                GEMINI_URL.format(model=config.model),
                headers={"x-goog-api-key": config.api_key, "content-type": "application/json"},
                json=_body(system, user, schema, max_tokens, level),
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


def _language_field(user, language):
    name = LANGUAGE_NAMES.get(language)
    if name:
        user["language"] = name
    return user


def call_plan(message, previous, config, *, post=None, history=None, language=""):
    """Return (plan dict | None, status string) for one chat message."""
    user = _language_field({"message": message[:MAX_QUESTION]}, language)
    turns = _clean_history(history)
    if turns:
        user["recent_turns"] = turns
    scope = _previous_scope(previous)
    if scope:
        user["previous_scope"] = scope
    return _generate(config, SYSTEM_PROMPT, user, SCHEMA, 400, post)


def interpret(message, previous, config, *, post=None, history=None, language=""):
    """Interpret one message. Returns an Intent or None; never raises."""
    if config is None or not isinstance(message, str) or not message.strip():
        return None
    try:
        plan, status = call_plan(message, previous, config, post=post, history=history, language=language)
        if plan is None:
            LOG.warning("gemini interpretation unavailable: %s", status)
            return None
        return intent_from_plan(plan, previous, message, history)
    except Exception:  # fail closed to the deterministic reply; log no content
        LOG.warning("gemini interpretation failed unexpectedly")
        return None


def phrase(message, facts, config, *, post=None, history=None, mode="", language=""):
    """Rewrite a database answer as a short chat reply.

    Returns ``(reply, show_details)`` or ``None``; never raises. ``None`` means the caller
    must send the plain database answer.
    """
    if config is None or not isinstance(message, str) or not message.strip() or not facts:
        return None
    try:
        user = _language_field({"message": message[:MAX_QUESTION], "facts": facts[:MAX_FACTS_CHARS]}, language)
        turns = _clean_history(history)
        if turns:
            user["recent_turns"] = turns
        system = PHRASE_PROMPT + (SUGGEST_ADDENDUM if mode == "suggest" else "")
        plan, status = _generate(config, system, user, PHRASE_SCHEMA, 500, post)
        if not isinstance(plan, dict):
            LOG.warning("gemini phrasing unavailable: %s", status)
            return None
        allowed = _numbers(facts[:MAX_FACTS_CHARS]) | _numbers(message) | _history_numbers(turns)
        reply = _safe_text(plan.get("reply"), allowed)
        if not reply:
            LOG.warning("gemini phrasing rejected by validation")
            return None
        return reply, plan.get("show_details") is True
    except Exception:
        LOG.warning("gemini phrasing failed unexpectedly")
        return None


def check_message(config, *, post=None):
    """Human-readable health check for the /llm command. Contains no secrets."""
    if config is None:
        return "Gemini is not configured (no valid GEMINI_API_KEY / model). Plain questions use the built-in parser only."
    plan, status = call_plan("golf under 15000 KM", None, config, post=post)
    intent = intent_from_plan(plan, None, "golf under 15000 KM") if plan else None
    if intent is None:
        return f"Gemini check failed: {status}.\nModel: {config.model}"
    return f"Gemini check: {status}.\nModel: {config.model}\nTest question understood as: {intent.kind}"
