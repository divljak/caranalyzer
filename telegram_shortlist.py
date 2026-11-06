"""Save cars to the shortlist from chat, backed by the web workspace's ``dealer_candidates``.

What the bot may do (and, with the proposed SQL, what its role is allowed to do):

* add a car **only if it is not already there** (``ON CONFLICT DO NOTHING``): it never touches a
  status or a note you set on the web;
* remove a car **only if it added it** (status still ``shortlisted`` with an empty note);
* read the list.

Until the owner applies ``docs/proposed-migrations/shortlist_write_access.sql`` the database refuses
the write; that refusal is reported as ``NotEnabled`` so the bot can say so plainly.
"""

import re

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telegram_bot import _age, _all, _is_fresh, _money, _one, _statement_timeout, _time
from telegram_i18n import t

LISTING_ID = re.compile(r"[0-9A-Za-z_-]{1,50}")
MAX_SHOWN = 8
INSUFFICIENT_PRIVILEGE = "42501"  # also what row-level security raises


class NotEnabled(Exception):
    """The database role has no permission to write the shortlist yet."""


def valid_listing_id(value):
    return isinstance(value, str) and LISTING_ID.fullmatch(value) is not None


def _is_permission_error(error):
    return getattr(getattr(error, "orig", None), "pgcode", None) == INSUFFICIENT_PRIVILEGE


def _describe(connection, listing_id):
    return _one(connection, """
      SELECT c.listing_id, coalesce(l.make,c.make) AS make, coalesce(l.model,c.model) AS model, c.year,
        CASE WHEN c.price_status='priced' AND c.price>0 THEN c.price END AS price
      FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
      WHERE c.listing_id=:id AND c.source='olx.ba' AND c.collection_method IN ('olx_api','olx_website')
    """, {"id": listing_id})


def name_for(row, lang="en"):
    name = " ".join(part for part in (row.get("make"), row.get("model")) if part) or t(lang, "unparsed_model")
    return f"{name} · {row['year']}" if row.get("year") else name


def label_for(row, lang="en"):
    return f"{name_for(row, lang)} · {_money(row.get('price'), lang)}"


def save_listing(engine, listing_id):
    """Returns ``(row, created)``, ``None`` for an unknown listing; raises NotEnabled without permission."""
    if not valid_listing_id(listing_id):
        return None
    try:
        with engine.begin() as connection:
            _statement_timeout(connection)
            row = _describe(connection, listing_id)
            if row is None:
                return None
            inserted = connection.execute(text("""
              INSERT INTO dealer_candidates(listing_id,status,note) VALUES (:id,'shortlisted','')
              ON CONFLICT(listing_id) DO NOTHING RETURNING listing_id
            """), {"id": listing_id}).scalar()
    except DBAPIError as error:
        if _is_permission_error(error):
            raise NotEnabled() from error
        raise
    return row, inserted is not None


def remove_listing(engine, listing_id):
    """Returns ``(row, outcome)`` with outcome ``removed``, ``kept`` (set up on the web) or ``gone``."""
    if not valid_listing_id(listing_id):
        return None, "gone"
    try:
        with engine.begin() as connection:
            _statement_timeout(connection)
            row = _describe(connection, listing_id) or {"listing_id": listing_id}
            current = _one(connection, "SELECT status,note FROM dealer_candidates WHERE listing_id=:id",
                           {"id": listing_id})
            if current is None:
                return row, "gone"
            if current["status"] != "shortlisted" or (current["note"] or "").strip():
                return row, "kept"
            connection.execute(text(
                "DELETE FROM dealer_candidates WHERE listing_id=:id AND status='shortlisted' AND note=''"),
                {"id": listing_id})
    except DBAPIError as error:
        if _is_permission_error(error):
            raise NotEnabled() from error
        raise
    return row, "removed"


def list_shortlist(engine):
    with engine.begin() as connection:
        _statement_timeout(connection)
        return _all(connection, f"""
          SELECT d.listing_id, d.status, d.saved_at, c.year, c.last_verified_at,
            coalesce(l.make,c.make) AS make, coalesce(l.model,c.model) AS model,
            CASE WHEN c.price_status='priced' AND c.price>0 THEN c.price END AS price,
            (SELECT s.asking_price FROM listing_snapshots s
             WHERE s.listing_id=d.listing_id AND s.price_status='priced' AND s.asking_price>0
               AND s.observed_at <= d.saved_at ORDER BY s.observed_at DESC LIMIT 1) AS price_then
          FROM dealer_candidates d
          JOIN car_listings c USING(listing_id)
          LEFT JOIN dealer_vehicle_labels l USING(listing_id)
          ORDER BY d.saved_at DESC LIMIT {MAX_SHOWN}
        """)


def shortlist_message(engine, lang="en"):
    """Text plus (listing_id, label) pairs for removal buttons."""
    rows = list_shortlist(engine)
    if not rows:
        return t(lang, "shortlist_empty"), []
    lines = [t(lang, "shortlist_title")]
    for row in rows:
        status = t(lang, f"cand_{row['status']}") if row["status"] in {"shortlisted", "contacted", "inspect", "passed"} else row["status"]
        lines.append(t(lang, "shortlist_row", label=name_for(row, lang),
                       price=_money(row["price"], lang), status=status))
        was, now = row.get("price_then"), row.get("price")
        if was and now and was != now:
            lines.append(t(lang, "shortlist_cut" if now < was else "shortlist_rise", was=_money(was, lang)))
        marker = "" if _is_fresh(row["last_verified_at"]) else "⚠ "
        seen = marker + t(lang, "seen_row", age=_age(row["last_verified_at"], lang),
                          when=_time(row["last_verified_at"], lang))
        lines.append(t(lang, "shortlist_seen", seen=seen, url=f"https://olx.ba/artikal/{row['listing_id']}"))
    lines.extend(["", t(lang, "shortlist_foot")])
    return "\n".join(lines), [(row["listing_id"], label_for(row, lang)) for row in rows]
