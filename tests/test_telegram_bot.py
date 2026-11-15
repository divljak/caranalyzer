import asyncio
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text
from starlette.responses import Response

import dealer_api

import telegram_bot
from telegram_intent import Filters, Intent, parse_question
from telegram_bot import (
    Html,
    TelegramAPIError,
    TelegramConfig,
    TelegramConfigurationError,
    command_for,
    help_message,
    keyboard,
    budget_message,
    compare_message,
    latest_message,
    process_update,
    status_message,
    trends_message,
)


ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
    "TELEGRAM_MARKET_URL": "https://cars.example/market",
}


class TelegramBotTests(unittest.TestCase):
    def test_configuration_is_explicit_and_fail_closed(self):
        config = TelegramConfig.from_env(ENV)
        self.assertEqual(-100123456789, config.chat_id)
        self.assertEqual(frozenset({123, 456}), config.allowed_user_ids)
        self.assertTrue(config.accepts_secret("test_only_webhook_secret_123"))
        self.assertFalse(config.accepts_secret("wrong"))
        with self.assertRaisesRegex(TelegramConfigurationError, "TELEGRAM_BOT_TOKEN"):
            TelegramConfig.from_env({})
        with self.assertRaisesRegex(TelegramConfigurationError, "integers"):
            TelegramConfig.from_env({**ENV, "TELEGRAM_CHAT_ID": "not-an-id"})
        with self.assertRaisesRegex(TelegramConfigurationError, "HTTPS"):
            TelegramConfig.from_env({**ENV, "TELEGRAM_MARKET_URL": "http://example.test"})

    def test_commands_are_bounded_and_unknown_text_returns_help(self):
        self.assertEqual(("status", ""), command_for("/status@CarBot"))
        self.assertEqual(("budget", "25000 Golf"), command_for("/budget 25000 Golf"))
        self.assertEqual(("trends", "30"), command_for("show trends for 30 days"))
        self.assertEqual(("latest", ""), command_for("show interesting finds"))
        self.assertEqual(("help", ""), command_for("invent a market prediction"))
        self.assertIn("Supabase evidence only", help_message())

    def test_keyboard_uses_callbacks_and_optional_https_market_link(self):
        config = TelegramConfig.from_env(ENV)
        markup = keyboard(config)
        flattened = [button for row in markup["inline_keyboard"] for button in row]
        self.assertIn("status", {button.get("callback_data") for button in flattened})
        self.assertIn("https://cars.example/market", {button.get("url") for button in flattened})

    def test_unauthorized_chat_or_user_is_silently_ignored(self):
        config = TelegramConfig.from_env(ENV)
        call = Mock()
        for chat_id, user_id in [(-100999, 123), (config.chat_id, 999)]:
            result = process_update({
                "update_id": 1,
                "message": {"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/help"},
            }, Mock(), config, call=call)
            self.assertEqual("unauthorized", result["reason"])
        call.assert_not_called()

    def test_authorized_help_and_callback_send_only_to_configured_group(self):
        config = TelegramConfig.from_env(ENV)
        calls = []

        def call(_config, method, payload):
            calls.append((method, payload))

        update = {
            "update_id": 2,
            "message": {
                "chat": {"id": config.chat_id},
                "from": {"id": 123},
                "text": "/help",
            },
        }
        result = process_update(update, Mock(), config, call=call)
        self.assertTrue(result["handled"])
        self.assertEqual("sendMessage", calls[0][0])
        self.assertEqual(config.chat_id, calls[0][1]["chat_id"])
        self.assertNotIn(config.bot_token, calls[0][1]["text"])

        calls.clear()
        callback = {
            "update_id": 3,
            "callback_query": {
                "id": "callback-test",
                "from": {"id": 123},
                "data": "trends:7",
                "message": {"chat": {"id": config.chat_id}},
            },
        }
        with patch("telegram_bot.render_command", return_value="database result") as render:
            result = process_update(callback, Mock(), config, call=call)
        self.assertTrue(result["handled"])
        render.assert_called_once()
        self.assertEqual(["answerCallbackQuery", "sendMessage"], [method for method, _ in calls])

    def test_production_middleware_opens_only_webhook_post(self):
        async def call_next(_request):
            return Response(status_code=204)

        def request(method):
            return SimpleNamespace(
                method=method,
                url=SimpleNamespace(path=dealer_api.TELEGRAM_WEBHOOK_PATH),
                headers={},
            )

        with patch.object(dealer_api, "HOSTED", True), \
             patch.dict(os.environ, {"VERCEL_ENV": "production"}):
            post = asyncio.run(dealer_api.private_requests(request("POST"), call_next))
            get = asyncio.run(dealer_api.private_requests(request("GET"), call_next))
        self.assertEqual(204, post.status_code)
        self.assertEqual(503, get.status_code)


class HtmlDeliveryTests(unittest.TestCase):
    def test_html_answers_use_parse_mode_and_never_show_link_previews(self):
        payload = telegram_bot.message_payload(1, Html("<b>Golf</b> &amp; Polo"))
        self.assertEqual(("HTML", "<b>Golf</b> &amp; Polo"), (payload["parse_mode"], payload["text"]))
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertEqual({"is_disabled": True}, payload["link_preview_options"])
        plain = telegram_bot.message_payload(1, "a < b")
        self.assertNotIn("parse_mode", plain)
        self.assertEqual("a < b", plain["text"])

    def test_html_that_would_not_fit_is_sent_as_plain_text_instead_of_cut_mid_tag(self):
        long = Html("<b>x</b>" + "<i>&amp;</i>" * 2000)
        payload = telegram_bot.message_payload(1, long)
        self.assertNotIn("parse_mode", payload)
        self.assertTrue(set(payload["text"]) <= {"x", "&"})
        self.assertLessEqual(len(payload["text"]), telegram_bot.MAX_MESSAGE)

    def test_a_rejected_html_message_is_resent_as_plain_text(self):
        sent = []

        def call(_config, method, payload):
            sent.append(payload)
            if payload.get("parse_mode"):
                raise TelegramAPIError("Telegram sendMessage returned an error")

        telegram_bot._deliver(call, TelegramConfig.from_env(ENV), 1, Html('<a href="u">Golf</a> &amp;'), None, None)
        self.assertEqual("Golf &", sent[-1]["text"])
        self.assertNotIn("parse_mode", sent[-1])

    def test_html_to_text_drops_link_targets_unless_asked(self):
        value = Html('<b><a href="https://olx.ba/artikal/1">Golf &amp; Co</a></b>')
        self.assertEqual("Golf & Co", telegram_bot.html_to_text(value))
        self.assertEqual("Golf & Co (https://olx.ba/artikal/1)", telegram_bot.html_to_text(value, keep_links=True))


class FamilyAndCallbackTests(unittest.TestCase):
    def test_family_and_include_older_survive_the_buttons(self):
        filters = Filters("", 10000, family=True, include_old=True)
        data = telegram_bot.refine_callback(filters, 5)
        self.assertEqual("fop:10000:0:0:5:", data)
        self.assertEqual((filters, 5), telegram_bot.parse_refine_page(data))
        self.assertEqual((Filters("golf", 15000), 0), telegram_bot.parse_refine_page("f:15000:0:0:0:golf"))
        self.assertIsNone(telegram_bot.parse_refine_page("fx:15000:0:0:0:golf"))

    def test_the_family_list_is_matched_as_whole_words(self):
        import re
        pattern = telegram_bot.FAMILY_REGEX.replace("\\m", r"\b").replace("\\M", r"\b")
        for label in ("skoda octavia", "volkswagen passat", "kia sportage", "ford s-max", "peugeot 5008"):
            self.assertRegex(label, pattern)
        for label in ("volkswagen golf", "fiat punto", "mercedes-benz e210", "opel corsa"):
            self.assertNotRegex(label, pattern)
        self.assertTrue(re.search(pattern, "bmw x3"))

    def test_lowercase_km_is_mileage_and_uppercase_km_is_price(self):
        year = datetime.now().year
        for message, expected in (
            ("golf do 15000 KM", Filters("golf", 15000)),
            ("golf do 15000km", Filters("golf", mileage_max=15000)),
            ("golf do 15.000 maraka", Filters("golf", 15000)),
            ("Octavia od 2016 do 150000 km", Filters("octavia", None, 2016, 150000)),
            ("passat do 12000 KM do 200000 km", Filters("passat", 12000, None, 200000)),
            ("golf staro do 10 godina", Filters("golf", year_min=year - 10)),
            ("golf ne starije od 8 godina do 9000 KM", Filters("golf", 9000, year - 8)),
        ):
            self.assertEqual(expected, parse_question(message).filters, message)

    def test_greetings_are_small_talk_and_easiest_to_sell_is_its_own_question(self):
        self.assertEqual("chitchat", parse_question("hey").kind)
        self.assertEqual("chitchat", parse_question("@mojagaraza_bot hej").kind)
        for message in ("koji auto se najlakse proda", "šta se najbrže prodaje do 15000 KM", "what sells fastest",
                        "koji su najlikvidniji auti"):
            self.assertEqual("liquidity", parse_question(message).kind, message)
        self.assertEqual("suggest", parse_question("imam 50k budzet, sta se najbolje prodaje").kind)

    def test_the_km_unit_question_offers_one_tap_answers(self):
        intent = parse_question("golf do 20000 Km")
        self.assertEqual(("clarify", "km_unit", 20000), (intent.kind, intent.note, intent.offset))
        text_value, choices = telegram_bot.answer_intent(Mock(), intent, TelegramConfig.from_env(ENV), lang="bs")
        self.assertEqual("Misliš li 20.000 KM (cijena) ili 20.000 km (kilometraža)?", text_value)
        self.assertEqual([("💰 Do 20.000 KM (cijena)", "f:20000:0:0:0:golf"),
                          ("🛣 Do 20.000 km (kilometraža)", "f:0:0:20000:0:golf")],
                         [row[0] for row in choices.rows])
        liquid = parse_question("sta se najlakse proda do 20000 Km")
        _, choices = telegram_bot.answer_intent(Mock(), liquid, TelegramConfig.from_env(ENV), lang="bs")
        self.assertEqual(["l:20000:0:0:", "l:0:0:20000:"], [row[0][1] for row in choices.rows])
        self.assertEqual(Filters(mileage_max=20000), telegram_bot.parse_liquidity_callback("l:0:0:20000:"))


def _naive_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def seed_family_scenario(engine, now):
    """Ogi's case: 'šta ima za porodicu do 10.000 KM'. Stale cheap cars, a 2022 'E210' typo, family models."""
    rows = [  # id, make, model, year, price, mileage, days since last seen
        ("__qa_stale1", "Audi", "A6", 2016, 9900, 210000, 40),
        ("__qa_stale2", "Škoda", "Octavia", 2015, 9500, 230000, 50),
        ("__qa_stale3", "Volkswagen", "Passat", 2015, 9800, 250000, 56),
        ("__qa_stale4", "Kia", "Sportage", 2014, 9990, 199000, 35),
        ("__qa_e210", "Mercedes-Benz", "E210", 2022, 6500, 399000, 1),
        ("__qa_oct", "Škoda", "Octavia", 2012, 9800, 210000, 1),
        ("__qa_pas", "Volkswagen", "Passat", 2011, 8900, 240000, 2),
        ("__qa_tou", "Volkswagen", "Touran", 2010, 9500, 230000, 3),
        ("__qa_golf", "Volkswagen", "Golf", 2014, 9900, 180000, 1),
        ("__qa_punto", "Fiat", "Punto", 2013, 4000, 150000, 1),
    ] + [(f"__qa_e{y}", "Mercedes-Benz", "E210", y, 5000 + (y - 1996) * 300, 300000, 4) for y in range(1996, 2004)]
    with engine.begin() as c:
        for lid, make, model, year, price, mileage, days in rows:
            c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,
                mileage,source,collection_method,last_verified_at,first_seen_at,is_active,
                consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,'priced',:mileage,
                'olx.ba','olx_api',:seen,:first,true,0)"""),
                {"id": lid, "make": make, "model": model, "year": year, "price": price, "mileage": mileage,
                 "seen": now - timedelta(days=days), "first": now - timedelta(days=days + 20)})
        c.execute(text("""INSERT INTO listing_snapshots(listing_id,observed_at,asking_price,price_status,
            source_url,title,is_active) VALUES ('__qa_oct',:seen,9800,'priced','https://olx.ba/artikal/x','t',true)"""),
            {"seen": now - timedelta(hours=1)})


def seed_liquidity_scenario(engine, now):
    """Golfs leave our search in ~6 days, Passats in ~30; Octavias have too few gone to rank."""
    rows = []
    for n in range(6):
        rows.append((f"__qa_lg{n}", "Volkswagen", "Golf", 2018, 15000, 120000 + n, 60 + n, 54 + n))  # 6 days
    for n in range(5):
        rows.append((f"__qa_lp{n}", "Volkswagen", "Passat", 2017, 16000, 150000, 90, 60))  # 30 days
    for n in range(3):
        rows.append((f"__qa_lo{n}", "Škoda", "Octavia", 2018, 15500, 110000, 40, 38))  # only 3 gone
    rows.append(("__qa_lact", "Volkswagen", "Golf", 2019, 16000, 90000, 30, 0))  # still listed
    rows.append(("__qa_lold", "Volkswagen", "Golf", 2008, 6000, 260000, 50, 45))  # older than 10 years
    rows.append(("__qa_lkm", "Volkswagen", "Golf", 2018, 15000, 900000, 50, 45))  # implausible mileage
    for n in range(8):  # still listed: most ads we see do not vanish, so the ranking is not collector churn
        rows.append((f"__qa_ly{n}", "Toyota", "Yaris", 2018, 14000, 100000, 20, 0))
    with engine.begin() as c:
        for lid, make, model, year, price, mileage, first_days, last_days in rows:
            c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,
                mileage,source,collection_method,last_verified_at,first_seen_at,is_active,
                consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,'priced',:mileage,
                'olx.ba','olx_api',:last,:first,true,0)"""),
                {"id": lid, "make": make, "model": model, "year": year, "price": price, "mileage": mileage,
                 "first": now - timedelta(days=first_days), "last": now - timedelta(days=last_days)})
        c.execute(text("""INSERT INTO listing_snapshots(listing_id,observed_at,asking_price,price_status,
            source_url,title,is_active) VALUES ('__qa_lact',:seen,16000,'priced','https://olx.ba/artikal/x','t',true)"""),
            {"seen": now - timedelta(hours=1)})


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class AnswerQualityDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.now = _naive_now()
        self.cleanup()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM listing_snapshots WHERE listing_id LIKE '__qa_%'"))
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__qa_%'"))

    def ids(self, reply):
        import re
        return re.findall(r"artikal/(__qa_\w+)", reply)

    def test_family_question_shows_fresh_roomy_models_and_hides_stale_ones(self):
        seed_family_scenario(self.engine, self.now)
        intent = parse_question("šta ima za porodicu do 10.000 KM")
        self.assertEqual(("find", Filters("", 10000, family=True)), (intent.kind, intent.filters))
        reply, page = telegram_bot.answer_intent(self.engine, intent, TelegramConfig.from_env(ENV), lang="bs")
        self.assertIsInstance(reply, Html)
        self.assertEqual(["__qa_oct", "__qa_pas", "__qa_tou"], self.ids(reply))  # seen 1, 2, 3 days ago
        self.assertTrue(reply.startswith("<b>Viđeni oglasi · do 10.000 KM · za porodicu</b>"))
        self.assertIn("Nemamo podatak o karoseriji", reply)
        self.assertIn("(viđeni prije 14–90 dana, možda više nisu aktivni): 4.", reply)
        self.assertEqual(4, page.older_hidden)
        buttons = [b for row in keyboard(TelegramConfig.from_env(ENV), page, "bs")["inline_keyboard"] for b in row]
        self.assertIn(("🕰 Uključi starije (4)", "fop:10000:0:0:0:"),
                      [(b["text"], b.get("callback_data")) for b in buttons])
        self.assertNotIn("https://olx.ba/artikal/__qa_oct\n", reply)  # the link sits on the car name
        self.assertIn('<a href="https://olx.ba/artikal/__qa_oct"><b>Škoda Octavia</b></a> · 2012 · <b>9.800 KM</b>', reply)

    def test_older_listings_come_back_on_request_ranked_after_fresh_ones(self):
        seed_family_scenario(self.engine, self.now)
        reply = telegram_bot.find_message(self.engine, Filters("", 10000, family=True, include_old=True), lang="bs")
        self.assertEqual(["__qa_oct", "__qa_pas", "__qa_tou", "__qa_stale4", "__qa_stale1"], self.ids(reply))
        self.assertIn("(uključeni i stariji)", reply)
        self.assertIn("⚠ Viđeno prije", reply)
        context = telegram_bot.context_from_reply(telegram_bot.html_to_text(reply))
        self.assertEqual(Filters("", 10000, family=True, include_old=True), context.filters)

    def test_implausible_listings_are_flagged_and_moved_last_not_hidden_or_changed(self):
        seed_family_scenario(self.engine, self.now)
        reply = telegram_bot.find_message(self.engine, Filters("mercedes", 10000), lang="bs")
        shown = self.ids(reply)
        self.assertNotEqual("__qa_e210", shown[0])  # before: first (newest year), now demoted
        page2 = telegram_bot.find_message(self.engine, Filters("mercedes", 10000), 5, lang="bs")
        all_ids = shown + self.ids(page2)
        self.assertIn("__qa_e210", all_ids)
        self.assertEqual("__qa_e210", all_ids[-1])  # demoted to the very end
        self.assertIn("⚠ Neobični podaci", page2)
        self.assertIn("podaci u bazi nisu mijenjani", page2)
        with self.engine.connect() as c:
            stored = c.execute(text("SELECT year,price,mileage FROM car_listings WHERE listing_id='__qa_e210'")).one()
        self.assertEqual((2022, 6500, 399000), tuple(stored))

    def test_easiest_to_sell_question_from_the_group(self):
        seed_liquidity_scenario(self.engine, self.now)
        intent = parse_question("Auto do 20000km, staro do 10 godina koje najlakse mogu prodati")
        self.assertEqual("liquidity", intent.kind)
        self.assertEqual(Filters(year_min=datetime.now().year - 10, mileage_max=20000), intent.filters)
        reply, _ = telegram_bot.answer_intent(self.engine, intent, TelegramConfig.from_env(ENV), lang="bs")
        self.assertIn("Nema dovoljno podataka", reply)  # every seeded car has more than 20.000 km
        intent = parse_question("Auto do 200000km, staro do 10 godina koje najlakse mogu prodati")
        reply, choices = telegram_bot.answer_intent(self.engine, intent, TelegramConfig.from_env(ENV), lang="bs")
        plain = telegram_bot.html_to_text(reply)
        self.assertIn("1. Volkswagen Golf: medijan 6 dana na OLX-u · nestalo 6 od 7 viđenih", plain)
        self.assertIn("2. Volkswagen Passat: medijan 30 dana na OLX-u · nestalo 5 od 5 viđenih", plain)
        self.assertIn("Vrijeme na OLX-u, ne prodaja", plain)
        self.assertNotIn("Octavia", plain)  # fewer than 5 gone: not ranked
        self.assertIn("nije potvrđena prodaja", plain)
        self.assertIn("Nije preporuka", plain)
        self.assertEqual("🔎 Volkswagen Golf", choices.rows[0][0][0])

    def test_suggestions_respect_family_and_skip_implausible_mileage(self):
        seed_family_scenario(self.engine, self.now)
        reply = telegram_bot.suggest_message(self.engine, Filters("", 10000, family=True), lang="bs")
        self.assertIn("za porodicu", reply.split("\n")[0])
        self.assertNotIn("Golf", reply)
        self.assertNotIn("E210", reply)


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class TelegramQuerySmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_all_database_commands_execute_against_current_schema(self):
        self.assertIn("no finished collector run", status_message(self.engine))
        self.assertIn("last 7 days", trends_message(self.engine, 7))
        self.assertIn("No matching listings", budget_message(self.engine, "25000 Golf"))
        self.assertIn("Database comparison", compare_message(
            self.engine, "Volkswagen Golf | Škoda Octavia"))
        self.assertIn("No collector run", latest_message(self.engine))


if __name__ == "__main__":
    unittest.main()
