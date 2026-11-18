"""Morning digest renderer, honesty rules, idempotency, and cron auth."""
import json
import os
from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import dealer_api
from telegram_bot import Html, TelegramAPIError, TelegramConfigurationError, html_to_text
from telegram_digest import (
    CUTS_SQL,
    CLEAN_SQL,
    RUN_SQL,
    SEEN_SQL,
    DigestConfig,
    FileLedger,
    HOT_SQL,
    load_digest_evidence,
    MemoryLedger,
    cron_authorized,
    handle_digest,
    in_send_window,
    plan_delivery,
    render_digest,
)


SARAJEVO = ZoneInfo("Europe/Sarajevo")
SECRET = "test_only_cron_secret_123"
TOKEN = "test-only-token"
ENV = {
    "TELEGRAM_BOT_TOKEN": TOKEN,
    "TELEGRAM_CHAT_ID": "-5332163835",
    "CRON_SECRET": SECRET,
    "BOT_TIMEZONE": "Europe/Sarajevo",
    "TELEGRAM_MARKET_URL": "https://cars.example/market",
    "TELEGRAM_DIGEST_SEND": "1",
}


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def evidence(**overrides):
    base = {
        "generated_at": utc("2026-11-05T08:45:00"),
        "run": {
            "status": "partial",
            "started_at": utc("2026-11-05T07:17:00"),
            "finished_at": utc("2026-11-05T07:21:00"),
            "search_attempted": 4,
            "detail_attempted": 6,
            "details_completed": 2,
            "observations": 110,
            "new_listings": 47,
            "errors": 5,
        },
        "clean_at": utc("2026-10-01T17:31:22"),
        "latest_snapshot": utc("2026-11-05T07:20:00"),
        "price_sample": 214,
        "price_drops": 3,
        "price_increases": 1,
        "notable_cuts": [
            {
                "listing_id": "1001",
                "make": "Volkswagen",
                "model": "Golf",
                "year": 2016,
                "asking_price": 18500,
                "previous_price": 19200,
                "observed_at": utc("2026-11-04T09:00:00"),
            },
            {
                "listing_id": "1002",
                "make": "Škoda",
                "model": "Octavia",
                "year": 2018,
                "asking_price": 24900,
                "previous_price": 26000,
                "observed_at": utc("2026-11-03T09:00:00"),
            },
        ],
        "hot": {"make": "Volkswagen", "n": 23, "total": 110},
        "first_seen": [
            {
                "listing_id": "2001",
                "make": "Audi",
                "model": "A4",
                "year": 2015,
                "price": 22000,
                "mileage": 160000,
                "n": 6,
                "med_price": 26800,
                "discount_pct": 18,
                "first_seen_at": utc("2026-11-05T07:18:00"),
            },
            {
                "listing_id": "2002",
                "make": "Renault",
                "model": "Megane",
                "year": 2017,
                "price": 16400,
                "mileage": 120000,
                "n": 5,
                "med_price": 19500,
                "discount_pct": 16,
                "first_seen_at": utc("2026-11-05T07:19:00"),
            },
        ],
    }
    base.update(overrides)
    return base


def visible(html_text):
    """What a reader sees (tags removed, entities decoded)."""
    return html_to_text(html_text)


class DigestRendererTests(unittest.TestCase):
    def test_partial_stale_digest_is_compact_and_honest(self):
        text = render_digest(evidence(), SARAJEVO)
        self.assertIsInstance(text, Html)
        lines = text.split("\n")
        self.assertEqual("🚗 <b>Garaža · četvrtak 5. nov</b>", lines[0])
        self.assertEqual("⚠️ Djelimičan prolaz kolektora · 47 novih oglasa · 5 grešaka"
                         " · cijene orijentaciono (zadnji čist prolaz 1. okt, 19:31)", lines[1])
        self.assertEqual(
            '📉 Najveći pad (7 dana): <a href="https://olx.ba/artikal/1001">Volkswagen Golf 2016</a> '
            "−700 KM → <b>18.500 KM</b> <s>19.200</s> (−4%)", lines[2])
        self.assertEqual(
            '🆕 <a href="https://olx.ba/artikal/2001">Audi A4 2015</a>: <b>22.000 KM</b> · '
            "18% ispod medijana 26.800 KM za 6 sličnih · 160.000 km", lines[3])
        self.assertIn("16% ispod medijana 19.500 KM za 5 sličnih · 120.000 km", lines[4])
        self.assertEqual("🔥 Najviše novih (24 h): Volkswagen (23 od 110)", lines[5])
        self.assertEqual(7, len(lines))
        self.assertTrue(lines[6].startswith("<i>Tražene cijene, ne prodajne."))
        self.assertIn("ne datum objave", lines[6])
        for gone in ("Detalji kolektora", "<blockquote", "…još", "Padovi cijena", "Najzanimljivije"):
            self.assertNotIn(gone, text)
        self.assertNotIn("olx.ba", visible(text))
        lowered = visible(text).lower()
        for banned in ("sold", "prodat", "newly posted", "novo objav", "deal", "preporuku", "najbolj"):
            self.assertNotIn(banned, lowered)

    def test_only_the_single_biggest_drop_and_two_standouts_are_shown(self):
        extra = dict(evidence()["first_seen"][0], listing_id="2003")
        text = render_digest(evidence(first_seen=evidence()["first_seen"] + [extra]), SARAJEVO)
        self.assertEqual(1, text.count("📉"))
        self.assertEqual(2, text.count("🆕"))
        self.assertNotIn("Škoda", text)

    def test_clean_run_is_one_green_line_and_hides_the_stale_warning(self):
        text = render_digest(evidence(
            clean_at=utc("2026-11-04T17:31:22"),
            run={
                "status": "completed",
                "started_at": utc("2026-11-05T07:17:00"),
                "finished_at": utc("2026-11-05T07:21:00"),
                "search_attempted": 3,
                "detail_attempted": 4,
                "details_completed": 4,
                "observations": 80,
                "new_listings": 76,
                "errors": 0,
            },
        ), SARAJEVO)
        self.assertEqual("✅ Sve radi · 76 novih oglasa · 0 grešaka", text.split("\n")[1])
        self.assertNotIn("⚠️", text)

    def test_a_run_limited_only_by_the_page_budget_reads_as_healthy(self):
        run = dict(evidence()["run"], status="partial", errors=0,
                   report=json.dumps({"stopped_reason": "discovery_budget_incomplete", "rejected": 0}))
        text = render_digest(evidence(run=run, clean_at=utc("2026-11-04T17:31:22")), SARAJEVO)
        self.assertEqual("✅ Sve radi · 47 novih oglasa · 0 grešaka · dnevni budžet stranica iskorišten",
                         text.split("\n")[1])
        self.assertNotIn("⚠️", text)

    def test_missing_standouts_and_hot_are_omitted(self):
        text = render_digest(evidence(first_seen=[], hot=None), SARAJEVO)
        self.assertNotIn("🆕", text)
        self.assertNotIn("🔥", text)
        self.assertIn("📉 Najveći pad", text)
        self.assertEqual(4, len(text.split("\n")))

    def test_no_drop_gets_a_short_line(self):
        text = render_digest(evidence(notable_cuts=[], price_drops=0), SARAJEVO)
        self.assertIn("📉 Nema padova cijena (7 dana)", text)

    def test_names_are_escaped_and_the_message_stays_short(self):
        cut = {"listing_id": "9<9", "make": "A&B <script>", "model": "M\"x", "year": 2020,
               "asking_price": 1000, "previous_price": 2000, "observed_at": utc("2026-11-04T09:00:00")}
        text = render_digest(evidence(notable_cuts=[cut] * 400, price_drops=400,
                                      hot={"make": "<b>X</b>", "n": 3, "total": 9}), SARAJEVO)
        self.assertLessEqual(len(text), 1500)
        self.assertNotIn("<script>", text)
        self.assertIn("A&amp;B &lt;script&gt; M&quot;x 2020", text)
        self.assertIn('href="https://olx.ba/artikal/9%3C9"', text)
        self.assertIn("&lt;b&gt;X&lt;/b&gt; (3 od 9)", text)

    def test_empty_digest_states_that_nothing_was_observed(self):
        text = render_digest({
            "generated_at": utc("2026-11-05T08:45:00"),
            "run": None,
            "clean_at": None,
            "latest_snapshot": None,
            "price_sample": 0,
            "price_drops": 0,
            "price_increases": 0,
            "notable_cuts": [],
            "first_seen": [],
        }, SARAJEVO)
        self.assertIn("⚠️ Nema završenog prolaza kolektora", text)
        self.assertIn("Nema padova cijena", text)
        self.assertNotIn("sold", text.lower())

    def test_queries_do_not_infer_sales_or_publish_dates(self):
        blob = "\n".join((RUN_SQL, CLEAN_SQL, CUTS_SQL, SEEN_SQL, HOT_SQL)).lower()
        self.assertIn("first_seen_at", blob)
        self.assertIn("asking_price<t.previous_price", blob.replace(" ", ""))
        for banned in ("sold", "published_date", "posted_date"):
            self.assertNotIn(banned, blob)

    def test_standout_query_orders_newest_first_and_limits_to_two(self):
        sql = " ".join(SEEN_SQL.split())
        self.assertIn("ORDER BY r.first_seen_at DESC,discount_pct DESC", sql)
        self.assertTrue(sql.endswith("LIMIT 2"))
        self.assertIn("r.price<=0.85*k.med_price", sql)

    def test_hot_query_counts_new_ads_per_brand_only(self):
        sql = " ".join(HOT_SQL.split()).lower()
        self.assertIn("first_seen_at>=:since", sql)
        self.assertNotIn("missing", sql)
        self.assertNotIn("is_active", sql)

    def test_load_evidence_runs_hot_query_with_a_24h_window(self):
        calls = []

        class Conn:
            def execute(self, statement, params=None):
                calls.append((str(statement), params))
                class Rows(list):
                    def first(self):
                        return None

                return Mock(scalar=Mock(return_value=None), mappings=Mock(return_value=Rows()))

        class Engine:
            def begin(self):
                outer = Mock()
                outer.__enter__ = Mock(return_value=Conn())
                outer.__exit__ = Mock(return_value=False)
                return outer

        result = load_digest_evidence(Engine(), utc("2026-11-05T08:45:00"))
        hot_calls = [params for sql, params in calls if "first_seen_at>=:since" in sql]
        self.assertEqual(1, len(hot_calls))
        self.assertEqual(utc("2026-11-04T08:45:00"), hot_calls[0]["since"])
        self.assertIsNone(result["hot"])
        self.assertTrue(any("statement_timeout" in sql for sql, _ in calls))


class DigestDeliveryTests(unittest.TestCase):
    def test_sarajevo_window_follows_winter_and_summer(self):
        self.assertTrue(in_send_window(utc("2026-11-05T08:45:00"), SARAJEVO))
        self.assertFalse(in_send_window(utc("2026-11-05T07:45:00"), SARAJEVO))
        self.assertTrue(in_send_window(utc("2026-07-05T07:45:00"), SARAJEVO))
        self.assertFalse(in_send_window(utc("2026-07-05T08:45:00"), SARAJEVO))

    def test_duplicate_send_is_skipped_and_failed_send_releases_the_day(self):
        ledger = MemoryLedger()
        now = utc("2026-11-05T08:45:00")
        first = plan_delivery(
            mode="send", now=now, tz=SARAJEVO, send_enabled=True,
            confirmed=False, ledger=ledger)
        second = plan_delivery(
            mode="send", now=now, tz=SARAJEVO, send_enabled=True,
            confirmed=False, ledger=ledger)
        self.assertEqual("scheduled", first["reason"])
        self.assertEqual("duplicate_send", second["reason"])
        ledger.release(first["day"])
        retry = plan_delivery(
            mode="test-send", now=now, tz=SARAJEVO, send_enabled=False,
            confirmed=True, ledger=ledger)
        self.assertEqual("test_send", retry["reason"])

    def test_file_ledger_rejects_a_second_claim_for_the_same_day(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = FileLedger(os.path.join(directory, "digest.json"))
            self.assertTrue(ledger.claim("2026-11-05"))
            self.assertFalse(ledger.claim("2026-11-05"))
            ledger.release("2026-11-05")
            self.assertTrue(ledger.claim("2026-11-05"))

    def test_disabled_and_unconfirmed_gates_do_not_claim_the_day(self):
        ledger = MemoryLedger()
        now = utc("2026-11-05T08:45:00")
        disabled = plan_delivery(
            mode="send", now=now, tz=SARAJEVO, send_enabled=False,
            confirmed=False, ledger=ledger)
        unconfirmed = plan_delivery(
            mode="test-send", now=now, tz=SARAJEVO, send_enabled=True,
            confirmed=False, ledger=ledger)
        outside = plan_delivery(
            mode="send", now=utc("2026-11-05T07:45:00"), tz=SARAJEVO,
            send_enabled=True, confirmed=False, ledger=ledger)
        self.assertEqual("send_disabled", disabled["reason"])
        self.assertEqual("test_send_unconfirmed", unconfirmed["reason"])
        self.assertEqual("outside_window", outside["reason"])
        claimed = plan_delivery(
            mode="send", now=now, tz=SARAJEVO, send_enabled=True,
            confirmed=False, ledger=ledger)
        self.assertEqual("scheduled", claimed["reason"])

    def test_handler_dry_render_and_one_test_send(self):
        ledger = MemoryLedger()
        calls = []

        def call(_config, method, payload):
            calls.append((method, payload))
            return {"message_id": 4}

        with patch("telegram_digest.load_digest_evidence", return_value=evidence()):
            dry_status, dry = handle_digest(
                Mock(), headers={"authorization": f"Bearer {SECRET}"}, mode="dry",
                environ=ENV, now=utc("2026-11-05T08:45:00"), ledger=ledger, call=call)
            self.assertEqual([], calls)
            test_status, sent = handle_digest(
                Mock(), headers={
                    "authorization": f"Bearer {SECRET}",
                    "x-digest-confirm": "SEND_TEST",
                }, mode="test-send", environ=ENV,
                now=utc("2026-11-05T06:00:00"), ledger=ledger, call=call)
            again_status, again = handle_digest(
                Mock(), headers={
                    "authorization": f"Bearer {SECRET}",
                    "x-digest-confirm": "SEND_TEST",
                }, mode="test-send", environ=ENV,
                now=utc("2026-11-05T12:00:00"), ledger=ledger, call=call)
        self.assertEqual(200, dry_status)
        self.assertEqual("dry_render", dry["reason"])
        self.assertIn("Djelimičan prolaz", dry["text"])
        self.assertTrue(dry["stale"])
        self.assertTrue(dry["partial"])
        self.assertEqual(200, test_status)
        self.assertEqual("test_send", sent["reason"])
        self.assertEqual(200, again_status)
        self.assertEqual("duplicate_send", again["reason"])
        self.assertEqual(1, len(calls))
        self.assertEqual("sendMessage", calls[0][0])
        self.assertEqual(-5332163835, calls[0][1]["chat_id"])
        self.assertNotIn(TOKEN, json.dumps(calls[0][1]))
        payload = calls[0][1]
        self.assertEqual("HTML", payload["parse_mode"])
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertEqual({"is_disabled": True}, payload["link_preview_options"])
        self.assertNotIn("reply_markup", payload)

    def test_failed_send_can_be_retried(self):
        ledger = MemoryLedger()

        def explode(_config, _method, _payload):
            raise TelegramAPIError("Telegram sendMessage request failed")

        with patch("telegram_digest.load_digest_evidence", return_value=evidence()):
            status, body = handle_digest(
                Mock(), headers={
                    "authorization": f"Bearer {SECRET}",
                    "x-digest-confirm": "SEND_TEST",
                }, mode="test-send", environ=ENV, now=utc("2026-11-05T08:45:00"),
                ledger=ledger, call=explode)
        self.assertEqual(502, status)
        self.assertEqual("telegram_send_failed", body["reason"])
        self.assertNotIn(TOKEN, json.dumps(body))
        retry = plan_delivery(
            mode="test-send", now=utc("2026-11-05T08:45:00"), tz=SARAJEVO,
            send_enabled=True, confirmed=True, ledger=ledger)
        self.assertEqual("test_send", retry["reason"])


class DigestAuthTests(unittest.TestCase):
    def test_cron_auth_fails_closed(self):
        self.assertFalse(cron_authorized({}, {}))
        self.assertFalse(cron_authorized(
            {"authorization": "Bearer short"}, {"CRON_SECRET": "short"}))
        self.assertFalse(cron_authorized(
            {"authorization": "Bearer wrong-secret-value"},
            {"CRON_SECRET": SECRET}))
        self.assertTrue(cron_authorized(
            {"authorization": f"Bearer {SECRET}"}, {"CRON_SECRET": SECRET}))
        self.assertTrue(cron_authorized(
            {"x-digest-secret": SECRET}, {"TELEGRAM_CRON_SECRET": SECRET}))

    def test_configuration_errors_do_not_echo_the_token(self):
        with self.assertRaises(TelegramConfigurationError) as missing:
            DigestConfig.from_env({})
        self.assertIn("TELEGRAM_BOT_TOKEN", str(missing.exception))
        with self.assertRaises(TelegramConfigurationError) as bad:
            DigestConfig.from_env({
                "TELEGRAM_BOT_TOKEN": "super-secret-token",
                "TELEGRAM_CHAT_ID": "nope",
            })
        self.assertNotIn("super-secret-token", str(bad.exception))
        with self.assertRaisesRegex(TelegramConfigurationError, "HTTPS"):
            DigestConfig.from_env({**ENV, "TELEGRAM_MARKET_URL": "http://example.test"})

    def test_route_requires_cron_auth_and_can_dry_render(self):
        headers = {"authorization": f"Bearer {SECRET}"}
        with patch.dict(os.environ, {**ENV, "TELEGRAM_CRON_SECRET": ""}, clear=False), \
             patch.object(dealer_api, "HOSTED", True), \
             patch.dict(os.environ, {"VERCEL_ENV": "production"}), \
             patch("telegram_digest.load_digest_evidence", return_value=evidence()):
            with TestClient(dealer_api.app) as client:
                denied = client.get(dealer_api.TELEGRAM_DIGEST_PATH)
                rendered = client.get(dealer_api.TELEGRAM_DIGEST_PATH, headers=headers)
        self.assertEqual(401, denied.status_code)
        self.assertNotIn("zapažanja", denied.text)
        self.assertEqual(200, rendered.status_code)
        body = rendered.json()
        self.assertEqual("dry_render", body["reason"])
        self.assertIn("Garaža", body["text"])
        self.assertNotIn(TOKEN, rendered.text)


if __name__ == "__main__":
    unittest.main()
