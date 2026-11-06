"""Visual digest card: rendering, caption, delivery with fallbacks, /digest, preview route."""

from datetime import datetime, timedelta, timezone
import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import dealer_api
import telegram_card
import telegram_digest
from telegram_bot import TelegramAPIError, TelegramConfig, process_update
from telegram_digest import (
    MemoryLedger, build_caption, deliver, handle_digest, load_digest_evidence, make_card,
    preview_card, send_card_to_chat,
)
from test_telegram_digest import ENV as DIGEST_ENV, SECRET, TOKEN, evidence, utc

TZ = ZoneInfo("Europe/Sarajevo")
PNG = b"\x89PNG\r\n\x1a\n"
BOT_ENV = {
    "TELEGRAM_BOT_TOKEN": TOKEN,
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
    "TELEGRAM_MARKET_URL": "https://cars.example/market",
}
CONFIG = TelegramConfig.from_env(BOT_ENV)


def rich_evidence(**overrides):
    base = evidence()
    now = base["generated_at"]
    base["recent_runs"] = [
        {"status": "partial", "started_at": now - timedelta(hours=1), "finished_at": now, "observations": 110,
         "new_listings": 47, "errors": 5},
        {"status": "completed", "started_at": now - timedelta(days=1, hours=1), "finished_at": now - timedelta(days=1),
         "observations": 180, "new_listings": 60, "errors": 0},
        {"status": "failed", "started_at": now - timedelta(days=2, hours=1), "finished_at": now - timedelta(days=2),
         "observations": 12, "new_listings": 0, "errors": 9},
    ]
    base["new_by_model"] = [{"label": "Volkswagen Golf", "n": 9}, {"label": "Škoda Octavia", "n": 7}]
    base.update(overrides)
    return base


def size(png):
    from PIL import Image
    return Image.open(io.BytesIO(png)).size


class CardRenderingTests(unittest.TestCase):
    def test_font_files_cover_bosnian_letters_and_symbols(self):
        from PIL import Image, ImageDraw, ImageFont
        for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
            font = ImageFont.truetype(str(telegram_card.FONT_DIR / name), 28)

            def glyph(ch):
                image = Image.new("L", (60, 60), 0)
                ImageDraw.Draw(image).text((5, 5), ch, font=font, fill=255)
                return image.tobytes()

            missing = glyph("￿")
            for char in "šđčćžŠĐČĆŽ·←−●↓↑":
                self.assertNotEqual(missing, glyph(char), f"{name} lacks {char}")
        self.assertTrue((telegram_card.FONT_DIR / "LICENSE_DEJAVU.txt").exists())

    def test_card_is_a_valid_png_of_the_expected_width(self):
        png = telegram_card.render_card(rich_evidence(), TZ)
        self.assertTrue(png.startswith(PNG))
        self.assertEqual(telegram_card.WIDTH, size(png)[0])
        self.assertGreater(size(png)[1], 1500)
        self.assertLess(len(png), 2_000_000)

    def test_card_renders_in_every_situation_without_raising(self):
        now = utc("2026-11-05T08:45:00")
        long_name = "Mercedes-Benz-Maybach Sedan Executive Long Wheelbase Limited Edition " * 3
        cases = {
            "nothing at all": {},
            "empty database": {"generated_at": now, "run": None, "recent_runs": [], "new_by_model": [],
                               "notable_cuts": [], "clean_at": None, "latest_snapshot": None},
            "failed run": rich_evidence(run={**evidence()["run"], "status": "failed", "errors": 9}),
            "clean run": rich_evidence(run={**evidence()["run"], "status": "completed", "errors": 0},
                                        clean_at=now - timedelta(hours=2)),
            "stale trends": rich_evidence(clean_at=None),
            "zero counts": rich_evidence(price_drops=0, price_increases=0, price_sample=0, new_by_model=[],
                                         notable_cuts=[]),
            "huge numbers": rich_evidence(price_drops=1234567, price_increases=7654321, price_sample=99999999),
            "very long names": rich_evidence(
                new_by_model=[{"label": long_name, "n": 3}],
                notable_cuts=[{"listing_id": "1", "make": long_name, "model": "", "year": 2016,
                               "asking_price": 100, "previous_price": 0, "observed_at": now}]),
            "missing fields": rich_evidence(
                notable_cuts=[{"listing_id": "1", "asking_price": 9000, "previous_price": 10000}],
                recent_runs=[{"status": None, "started_at": None, "observations": None, "new_listings": None}]),
            "one run only": rich_evidence(recent_runs=rich_evidence()["recent_runs"][:1]),
            "naive timestamps": rich_evidence(generated_at=datetime(2026, 11, 5, 8, 45)),
        }
        for label, data in cases.items():
            png = telegram_card.render_card(data, TZ)
            self.assertTrue(png.startswith(PNG), label)

    def test_state_label_mirrors_the_text_digest(self):
        self.assertEqual("NEMA PODATAKA", telegram_card.card_state({})[0])
        self.assertEqual("DJELIMIČNO", telegram_card.card_state(evidence())[0])
        clean = {"run": {"status": "completed", "errors": 0}}
        self.assertEqual("ČISTO", telegram_card.card_state(clean)[0])
        self.assertEqual("NEUSPJELO", telegram_card.card_state({"run": {"status": "failed"}})[0])
        self.assertEqual("DJELIMIČNO", telegram_card.card_state({"run": {"status": "completed", "errors": 2}})[0])

    def test_make_card_returns_none_instead_of_raising(self):
        with patch("telegram_card.render_card", side_effect=OSError("font missing")):
            with self.assertLogs("telegram_digest", level="WARNING") as logs:
                self.assertIsNone(make_card(rich_evidence(), TZ))
        self.assertNotIn(TOKEN, " ".join(logs.output))
        self.assertIsNotNone(make_card(rich_evidence(), TZ))


class CaptionTests(unittest.TestCase):
    def test_caption_summarises_and_links_the_biggest_cut(self):
        caption = build_caption(rich_evidence(), TZ)
        self.assertIn("<b>Garaža · čet, 5. nov</b>", caption)
        self.assertIn("djelimično", caption)
        self.assertIn("<b>110</b> zapažanja", caption)
        self.assertIn('<a href="https://olx.ba/artikal/1001">Volkswagen Golf · 2016</a>', caption)
        self.assertIn("Nestanak iz pretrage nije prodaja", caption)
        self.assertLessEqual(len(caption), telegram_digest.MAX_CAPTION_CHARS)

    def test_caption_says_when_trends_are_suppressed(self):
        self.assertIn("Trend potisnut", build_caption(rich_evidence(clean_at=None), TZ))

    def test_every_dynamic_value_is_html_escaped(self):
        hostile = rich_evidence(notable_cuts=[{
            "listing_id": '1"><script>', "make": "<b>Evil</b>", "model": "&co", "year": 2016,
            "asking_price": 100, "previous_price": 200, "observed_at": utc("2026-11-04T09:00:00")}])
        caption = build_caption(hostile, TZ)
        self.assertNotIn("<script>", caption)
        self.assertNotIn("<b>Evil</b>", caption)
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt;", caption)
        self.assertIn("&amp;co", caption)

    def test_caption_is_capped(self):
        many = rich_evidence(notable_cuts=[{
            "listing_id": "1", "make": "A" * 2000, "model": "B", "year": 1, "asking_price": 1,
            "previous_price": 2, "observed_at": utc("2026-11-04T09:00:00")}])
        self.assertLessEqual(len(build_caption(many, TZ)), telegram_digest.MAX_CAPTION_CHARS)

    def test_empty_database_caption(self):
        caption = build_caption({"generated_at": utc("2026-11-05T08:45:00")}, TZ)
        self.assertIn("Nema završenog prolaza kolektora", caption)


class DeliveryTests(unittest.TestCase):
    def deliver(self, upload, call=None, png=PNG + b"x", note=""):
        call = call or Mock(return_value={"message_id": 1})
        shown, result = deliver(CONFIG, 123, CONFIG.market_url, rich_evidence(), TZ, "plain text",
                                call=call, upload=upload, png=png, note=note)
        return shown, call

    def test_card_is_sent_as_a_photo_with_caption_and_buttons(self):
        upload = Mock(return_value={"message_id": 9})
        shown, call = self.deliver(upload)
        self.assertEqual("card", shown)
        config, method, fields, files = upload.call_args.args
        self.assertEqual("sendPhoto", method)
        self.assertEqual({"chat_id": "123", "caption": build_caption(rich_evidence(), TZ), "parse_mode": "HTML",
                          "reply_markup": json.dumps(telegram_digest.digest_keyboard(CONFIG.market_url))}, fields)
        self.assertEqual("image/png", files["photo"][2])
        self.assertTrue(files["photo"][1].startswith(PNG))
        call.assert_not_called()
        self.assertNotIn(TOKEN, json.dumps(fields))

    def test_text_digest_is_sent_when_there_is_no_picture(self):
        shown, call = self.deliver(Mock(), png=None)
        self.assertEqual("text", shown)
        method, payload = call.call_args.args[1:]
        self.assertEqual(("sendMessage", "plain text"), (method, payload["text"]))
        shown, call = self.deliver(None)  # uploads disabled
        self.assertEqual("text", shown)

    def test_failed_photo_upload_falls_back_to_text(self):
        upload = Mock(side_effect=TelegramAPIError("Telegram sendPhoto upload failed"))
        with self.assertLogs("telegram_digest", level="WARNING"):
            shown, call = self.deliver(upload, note="Nota\n\n")
        self.assertEqual("text", shown)
        self.assertTrue(call.call_args.args[2]["text"].startswith("Nota\n\nplain text"))

    def test_text_failure_after_photo_failure_propagates(self):
        upload = Mock(side_effect=TelegramAPIError("x"))
        call = Mock(side_effect=TelegramAPIError("y"))
        with self.assertLogs("telegram_digest", level="WARNING"):
            with self.assertRaises(TelegramAPIError):
                self.deliver(upload, call=call)


class ScheduledSendGatingTests(unittest.TestCase):
    """The morning digest keeps the exact same gates; only the format of an allowed send changes."""

    def run_digest(self, mode, *, now="2026-11-05T08:45:00", confirm=False, env=None, ledger=None,
                   upload=None, call=None):
        headers = {"authorization": f"Bearer {SECRET}"}
        if confirm:
            headers["x-digest-confirm"] = "SEND_TEST"
        with patch("telegram_digest.load_digest_evidence", return_value=rich_evidence()):
            return handle_digest(Mock(), headers=headers, mode=mode, environ=env or DIGEST_ENV,
                                 now=utc(now), ledger=ledger or MemoryLedger(),
                                 call=call or Mock(return_value={"message_id": 4}), upload=upload)

    def test_nothing_is_uploaded_unless_the_existing_gates_allow_a_send(self):
        upload = Mock(return_value={"message_id": 1})
        no_send = {k: v for k, v in DIGEST_ENV.items() if k != "TELEGRAM_DIGEST_SEND"}
        self.run_digest("dry", upload=upload)
        self.run_digest("send", env=no_send, upload=upload)                         # disarmed (current production state)
        self.run_digest("send", now="2026-11-05T05:00:00", upload=upload)          # outside the window
        self.run_digest("test-send", upload=upload)                                # unconfirmed
        self.run_digest("whatever", upload=upload)
        upload.assert_not_called()

    def test_an_allowed_send_uses_the_card_and_is_still_once_per_day(self):
        upload = Mock(return_value={"message_id": 12})
        call = Mock()
        ledger = MemoryLedger()
        status, body = self.run_digest("test-send", confirm=True, ledger=ledger, upload=upload, call=call)
        again_status, again = self.run_digest("test-send", confirm=True, ledger=ledger, upload=upload, call=call)
        self.assertEqual((200, "card", 12), (status, body["format"], body["message_id"]))
        self.assertEqual("duplicate_send", again["reason"])
        upload.assert_called_once()
        call.assert_not_called()

    def test_a_scheduled_send_inside_the_window_uses_the_card(self):
        upload = Mock(return_value={"message_id": 3})
        status, body = self.run_digest("send", now="2026-11-05T08:45:00", upload=upload)
        self.assertEqual(("scheduled", "card"), (body["reason"], body["format"]))

    def test_photo_failure_still_delivers_text_and_both_failing_releases_the_day(self):
        ledger = MemoryLedger()
        upload = Mock(side_effect=TelegramAPIError("x"))
        with self.assertLogs("telegram_digest", level="WARNING"):
            status, body = self.run_digest("test-send", confirm=True, ledger=ledger, upload=upload)
        self.assertEqual((200, "text"), (status, body["format"]))
        ledger2 = MemoryLedger()
        call = Mock(side_effect=TelegramAPIError("y"))
        with self.assertLogs("telegram_digest", level="WARNING"):
            status, body = self.run_digest("test-send", confirm=True, ledger=ledger2, upload=upload, call=call)
        self.assertEqual((502, "telegram_send_failed"), (status, body["reason"]))
        self.assertTrue(ledger2.claim("2026-11-05"))  # released, so a retry is possible

    def test_a_fake_call_never_triggers_a_real_upload(self):
        with patch("telegram_digest.telegram_upload") as real_upload:
            status, body = self.run_digest("test-send", confirm=True, upload=telegram_digest._AUTO)
        real_upload.assert_not_called()
        self.assertEqual("text", body["format"])


def dm(text_value, *, user=123, chat_id=123, chat_type="private", uid=1):
    return {"update_id": uid, "message": {"chat": {"id": chat_id, "type": chat_type},
                                           "from": {"id": user}, "text": text_value}}


class DigestCommandTests(unittest.TestCase):
    def run_command(self, update, **kwargs):
        call, upload = Mock(), Mock(return_value={"message_id": 1})
        with patch("telegram_digest.load_digest_evidence", return_value=rich_evidence()):
            result = process_update(update, Mock(), CONFIG, call=call, llm=None, phraser=None,
                                    upload=kwargs.get("upload", upload))
        return result, call, upload

    def test_authorized_user_gets_the_card_in_the_chat_they_asked_from(self):
        result, call, upload = self.run_command(dm("/digest"))
        self.assertEqual(("digest", "card"), (result["command"], result["format"]))
        self.assertEqual("123", upload.call_args.args[2]["chat_id"])
        call.assert_not_called()
        group = dm("/digest", chat_id=CONFIG.chat_id, chat_type="supergroup")
        _, _, upload = self.run_command(group)
        self.assertEqual(str(CONFIG.chat_id), upload.call_args.args[2]["chat_id"])

    def test_it_works_even_though_the_morning_send_is_disarmed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_DIGEST_SEND", None)
            result, _, upload = self.run_command(dm("/digest"))
        self.assertEqual("card", result["format"])
        upload.assert_called_once()

    def test_strangers_and_spoofed_chats_get_nothing(self):
        for update in (dm("/digest", user=999, chat_id=999), dm("/digest", user=123, chat_id=999),
                       dm("/digest", user=123, chat_id=123, chat_type="group")):
            result, call, upload = self.run_command(update)
            self.assertEqual("unauthorized", result["reason"])
            call.assert_not_called()
            upload.assert_not_called()

    def test_text_fallback_explains_why_when_the_picture_cannot_be_made(self):
        call, upload = Mock(), Mock()
        with patch("telegram_digest.load_digest_evidence", return_value=rich_evidence()), \
                patch("telegram_digest.make_card", return_value=None):
            result = process_update(dm("/digest"), Mock(), CONFIG, call=call, llm=None, phraser=None, upload=upload)
        self.assertEqual("text", result["format"])
        upload.assert_not_called()
        self.assertTrue(call.call_args.args[2]["text"].startswith("Kartica nije mogla biti napravljena"))

    def test_without_an_upload_function_it_sends_the_plain_text_digest(self):
        call = Mock()
        with patch("telegram_digest.load_digest_evidence", return_value=rich_evidence()):
            result = process_update(dm("/digest"), Mock(), CONFIG, call=call, llm=None, phraser=None, upload=None)
        self.assertEqual("text", result["format"])
        self.assertNotIn("Kartica nije", call.call_args.args[2]["text"])

    def test_help_lists_the_command(self):
        from telegram_bot import help_message
        self.assertIn("/digest", help_message())


class PreviewRouteTests(unittest.TestCase):
    def test_preview_requires_the_cron_secret_and_never_sends(self):
        call = Mock()
        with patch("telegram_digest.load_digest_evidence", return_value=rich_evidence()):
            self.assertEqual(401, preview_card(Mock(), headers={}, environ=DIGEST_ENV)[0])
            status, body = preview_card(Mock(), headers={"authorization": f"Bearer {SECRET}"}, environ=DIGEST_ENV)
        self.assertEqual(200, status)
        self.assertTrue(body.startswith(PNG))
        call.assert_not_called()

    def test_route_serves_png_only_with_the_secret(self):
        with patch.dict(os.environ, DIGEST_ENV), patch.object(dealer_api, "HOSTED", False), \
                patch("telegram_digest.load_digest_evidence", return_value=rich_evidence()), \
                patch("telegram_bot.telegram_call") as send:
            client = TestClient(dealer_api.app)
            self.assertEqual(401, client.get("/api/telegram/digest?format=png").status_code)
            ok = client.get("/api/telegram/digest?format=png", headers={"authorization": f"Bearer {SECRET}"})
        self.assertEqual(200, ok.status_code)
        self.assertEqual("image/png", ok.headers["content-type"])
        self.assertEqual("no-store", ok.headers["cache-control"])
        self.assertTrue(ok.content.startswith(PNG))
        send.assert_not_called()

    def test_route_reports_unavailable_when_the_card_cannot_be_drawn(self):
        with patch.dict(os.environ, DIGEST_ENV), patch.object(dealer_api, "HOSTED", False), \
                patch("telegram_digest.load_digest_evidence", return_value=rich_evidence()), \
                patch("telegram_digest.make_card", return_value=None):
            client = TestClient(dealer_api.app)
            response = client.get("/api/telegram/digest?format=png", headers={"authorization": f"Bearer {SECRET}"})
        self.assertEqual(503, response.status_code)


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class DigestEvidenceDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.addCleanup(self.cleanup)
        self.cleanup()
        self.now = datetime.now(timezone.utc).replace(tzinfo=None)

    def cleanup(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM listing_snapshots WHERE listing_id LIKE '__tg_%'"))
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__tg_%'"))
            c.execute(text("DELETE FROM website_collection_runs WHERE session_id LIKE '__tg_%'"))

    def seed(self):
        runs = [(0, "partial", 110, 5, 0), (1, "completed", 180, 60, 0), (2, "failed", 12, 0, 9)]
        with self.engine.begin() as c:
            for days, status, obs, new, errors in runs:
                c.execute(text("""INSERT INTO website_collection_runs(session_id,started_at,finished_at,status,
                    source_query,search_limit,detail_limit,search_attempted,detail_attempted,discovery_complete,
                    details_completed,observations,new_listings,errors) VALUES (:s,:a,:b,:st,'q',3,20,1,2,1,2,
                    :obs,:new,:err)"""),
                    {"s": f"__tg_run{days}", "a": self.now - timedelta(days=days, hours=1),
                     "b": self.now - timedelta(days=days), "st": status, "obs": obs, "new": new, "err": errors})
            for lid, model, minutes in (("__tg_m1", "Golf", 30), ("__tg_m2", "Golf", 20), ("__tg_m3", "Octavia", 10)):
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,
                    source,collection_method,last_verified_at,first_seen_at,is_active,consecutive_missing_runs)
                    VALUES (:id,:make,:model,2016,12000,'priced',150000,'olx.ba','olx_api',:seen,:seen,true,0)"""),
                    {"id": lid, "make": "Volkswagen" if model == "Golf" else "Škoda", "model": model,
                     "seen": self.now - timedelta(minutes=minutes)})

    def test_evidence_contains_recent_runs_and_the_new_listing_mix(self):
        self.seed()
        data = load_digest_evidence(self.engine, datetime.now(timezone.utc))
        self.assertEqual(["partial", "completed", "failed"], [r["status"] for r in data["recent_runs"]])
        self.assertEqual([("Volkswagen Golf", 2), ("Škoda Octavia", 1)],
                         [(m["label"], m["n"]) for m in data["new_by_model"]])
        self.assertTrue(telegram_card.render_card(data, TZ).startswith(PNG))

    def test_loading_evidence_never_changes_the_database(self):
        self.seed()
        with self.engine.connect() as c:
            before = c.execute(text("SELECT (SELECT count(*) FROM car_listings),(SELECT count(*) FROM "
                                    "website_collection_runs)")).one()
        load_digest_evidence(self.engine, datetime.now(timezone.utc))
        with self.engine.connect() as c:
            after = c.execute(text("SELECT (SELECT count(*) FROM car_listings),(SELECT count(*) FROM "
                                   "website_collection_runs)")).one()
        self.assertEqual(tuple(before), tuple(after))


if __name__ == "__main__":
    unittest.main()
