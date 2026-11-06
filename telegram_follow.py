"""Follow a model from chat, backed by the same table as the web Following page.

The bot's database role may insert and delete rows in ``dealer_model_searches`` (that is how the
web workspace follows a model); it can write nothing else. A model is followed only after the
user taps a confirm button, and the button's text is never trusted: the make and model are
checked against real listings before anything is saved.

Models are identified exactly like the web workspace does (``dealer_api.LISTING_SOURCE``'s
``market_make`` / ``market_model``) so a model followed here shows up there and vice versa.
"""

from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from sqlalchemy import text

from telegram_bot import _all, _money, _one, _statement_timeout, _time, _like_pattern
from telegram_i18n import t
from telegram_intent import fold

MAX_CANDIDATES = 3
MAX_FOLLOWS_SHOWN = 8
HEX32 = re.compile(r"[0-9a-f]{32}")
_RECENT = """c.source='olx.ba'
  AND c.collection_method IN ('olx_api','olx_website')
  AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'"""


def _source():
    import dealer_api  # lazy: dealer_api imports the bot, so it cannot be imported at module load

    return dealer_api.LISTING_SOURCE


def _tokens(search):
    return [fold(token) for token in re.split(r"\s+", search or "") if token][:3]


def resolve_models(engine, search, limit=MAX_CANDIDATES):
    """Real (make, model, listing count) combinations whose name contains every word in ``search``."""
    tokens = _tokens(search)
    if not tokens:
        return []
    params = {f"t{i}": _like_pattern(token) for i, token in enumerate(tokens)}
    params["limit"] = int(limit)
    conditions = " AND ".join(
        f"translate(lower(concat_ws(' ',c.market_make,c.market_model)),'šđčćž','sdccz') LIKE :t{i} ESCAPE '\\'"
        for i in range(len(tokens)))
    with engine.begin() as connection:
        _statement_timeout(connection)
        return _all(connection, f"""
          SELECT trim(c.market_make) AS make, trim(c.market_model) AS model, count(*) AS n
          FROM {_source()}
          WHERE {_RECENT}
            AND trim(coalesce(c.market_make,''))<>'' AND trim(coalesce(c.market_model,''))<>''
            AND {conditions}
          GROUP BY 1,2 ORDER BY n DESC, 1, 2 LIMIT :limit
        """, params)


def _canonical(connection, make, model):
    """The make/model exactly as stored (so differently-cased spellings follow the same model),
    or ``None`` when no recent listing has them."""
    return _one(connection, f"""
      SELECT trim(c.market_make) AS make, trim(c.market_model) AS model FROM {_source()}
      WHERE {_RECENT}
        AND lower(trim(c.market_make))=lower(trim(:make)) AND lower(trim(c.market_model))=lower(trim(:model))
      ORDER BY 1, 2 LIMIT 1
    """, {"make": make, "model": model})


def _label(make, model):
    return f"{make} {model}".strip()


def follow_model(engine, make, model):
    """Follow a model. Returns ``(search_id, created)`` or ``None`` when no such model has listings."""
    import dealer_api
    from fastapi import HTTPException

    make, model = (make or "").strip(), (model or "").strip()
    if not make or not model or len(make) > 80 or len(model) > 100:
        return None
    with engine.begin() as connection:
        _statement_timeout(connection)
        canonical = _canonical(connection, make, model)
        if canonical is None:
            return None
        make, model = canonical["make"], canonical["model"]
        filters = dealer_api.Filters(make=make, model=model).model_copy(update={"price_max": None})
        serialized = json.dumps(filters.model_dump(), sort_keys=True)
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        existing = _one(connection, "SELECT id FROM dealer_model_searches WHERE fingerprint=:fp",
                        {"fp": fingerprint})
        if existing:
            return existing["id"], False
        try:
            evidence = dealer_api.calculate_comparison(connection, dealer_api.Comparison(
                segments=[dealer_api.Segment(make=make, model=model)], filters=filters))
            baseline, anchor = evidence["segments"][0], dealer_api.as_of(connection)
        except HTTPException:  # no clean collection to anchor a baseline on yet
            baseline, anchor = {"make": make, "model": model}, None
        anchor = anchor or datetime.now(timezone.utc).replace(tzinfo=None)
        row = _one(connection, """
          INSERT INTO dealer_model_searches(id,fingerprint,filters,baseline,baseline_as_of)
          VALUES (:id,:fp,CAST(:filters AS jsonb),CAST(:baseline AS jsonb),:anchor)
          ON CONFLICT(fingerprint) DO UPDATE SET fingerprint=excluded.fingerprint RETURNING id
        """, {"id": uuid.uuid4().hex, "fp": fingerprint, "filters": serialized,
              "baseline": json.dumps(baseline), "anchor": anchor})
    return row["id"], True


def unfollow(engine, search_id):
    """Stop following. Returns the model's label, or ``None`` if it was not followed."""
    if not isinstance(search_id, str) or not HEX32.fullmatch(search_id):
        return None
    with engine.begin() as connection:
        row = _one(connection, "SELECT filters FROM dealer_model_searches WHERE id=:id", {"id": search_id})
        if row is None:
            return None
        connection.execute(text("DELETE FROM dealer_model_searches WHERE id=:id"), {"id": search_id})
    filters = row["filters"] if isinstance(row["filters"], dict) else json.loads(row["filters"])
    return _label(filters.get("make", ""), filters.get("model", ""))


def list_follows(engine):
    with engine.begin() as connection:
        _statement_timeout(connection)
        rows = _all(connection, f"""
          SELECT id, filters, saved_at FROM dealer_model_searches
          ORDER BY saved_at DESC LIMIT {MAX_FOLLOWS_SHOWN}
        """)
        for row in rows:
            filters = row["filters"] if isinstance(row["filters"], dict) else json.loads(row["filters"])
            row["make"], row["model"] = filters.get("make", ""), filters.get("model", "")
            stats = _one(connection, f"""
              SELECT count(*) AS n,
                percentile_cont(.5) WITHIN GROUP (ORDER BY c.price)
                  FILTER (WHERE c.price_status='priced' AND c.price>0) AS median,
                count(*) FILTER (WHERE c.first_seen_at > :saved_at) AS new_n
              FROM {_source()}
              WHERE {_RECENT}
                AND lower(trim(c.market_make))=lower(trim(:make)) AND lower(trim(c.market_model))=lower(trim(:model))
                AND c.year BETWEEN :year_min AND :year_max
            """, {"make": row["make"], "model": row["model"], "saved_at": row["saved_at"],
                  "year_min": filters.get("year_min", 1900), "year_max": filters.get("year_max", 2100)})
            row["stats"] = stats or {"n": 0, "median": None, "new_n": 0}
    return rows


def following_message(engine, lang="en"):
    """Text and the follow list (id, label) for building unfollow buttons."""
    rows = list_follows(engine)
    if not rows:
        return t(lang, "following_empty"), []
    lines = [t(lang, "following_title")]
    for row in rows:
        label, stats, when = _label(row["make"], row["model"]), row["stats"], _time(row["saved_at"], lang)
        if stats["n"]:
            lines.append(t(lang, "following_row", label=label, n=stats["n"], median=_money(stats["median"], lang),
                           new=stats["new_n"], when=when))
        else:
            lines.append(t(lang, "following_row_nodata", label=label, when=when))
    lines.extend(["", t(lang, "following_foot1"), t(lang, "following_foot2")])
    return "\n".join(lines), [(row["id"], _label(row["make"], row["model"])) for row in rows]
