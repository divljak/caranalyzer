"""Natural-language question handling for the private Telegram bot (Phase 2)."""

from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

import telegram_bot
from telegram_bot import (
    DB_UNAVAILABLE,
    TelegramConfig,
    find_message,
    keyboard,
    model_stats_message,
    parse_refine_callback,
    process_update,
    refine_callback,
    status_message,
    compare_message,
)
from telegram_intent import Filters, Intent, context_from_reply, parse_question

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)
BOT_ANSWER = "Observed listings · Golf · up to 15.000 KM\nShowing 3 of 3 database matches"


def message(text_value, *, user=123, chat=None, reply=None, uid=10):
    body = {
        "chat": {"id": CONFIG.chat_id if chat is None else chat},
        "from": {"id": user},
        "text": text_value,
    }
    if reply is not None:
        body["reply_to_message"] = reply
    return {"update_id": uid, "message": body}


def bot_reply(text_value=BOT_ANSWER):
    return {"from": {"id": 999, "is_bot": True, "username": "mojagaraza_bot"}, "text": text_value}


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    os.environ.pop("GEMINI_API_KEY", None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


class ParserTests(unittest.TestCase):
    def kinds(self, value, context=None):
        return parse_question(value, context).kind

    def test_buying_questions_extract_only_stated_filters(self):
        intent = parse_question("Škoda Octavia from 2016 under 150k km mileage and 20k KM")
        self.assertEqual("find", intent.kind)
        self.assertEqual(Filters("škoda octavia", 20000, 2016, 150000), intent.filters)
        self.assertEqual(Filters("golf", 15000), parse_question("golf under 15000 KM").filters)
        self.assertEqual(Filters("golf", 12000), parse_question("ima li golf do 12.000 KM").filters)
        self.assertEqual(Filters("bmw 320d", 25000, 2015), parse_question("bmw 320d 2015 25000").filters)
        self.assertEqual(Filters("octavia", 25000), parse_question("octavia 25 000 KM").filters)

    def test_open_advice_asks_for_a_budget_instead_of_inventing_a_pick(self):
        self.assertEqual("clarify", self.kinds("@mojagaraza_bot what car should I buy"))
        self.assertEqual("clarify", self.kinds("preporuci mi auto"))

    def test_other_languages_and_intents(self):
        self.assertEqual("trends", self.kinds("kako se mijenjaju cijene zadnjih 14 dana"))
        self.assertEqual(14, parse_question("kako se mijenjaju cijene zadnjih 14 dana").days)
        self.assertEqual("compare", self.kinds("uporedi golf i passat"))
        self.assertEqual(("golf", "octavia"), parse_question("Golf vs Octavia").labels)
        self.assertEqual("model_stats", self.kinds("koliko košta passat"))
        self.assertEqual("latest", self.kinds("what's new"))
        self.assertEqual("status", self.kinds("is the data fresh?"))

    def test_forecasts_and_foreign_currency_are_refused_not_guessed(self):
        forecast = parse_question("what will prices be next year")
        self.assertEqual(("unsupported", "forecast"), (forecast.kind, forecast.note))
        currency = parse_question("golf under 10000 eur")
        self.assertEqual(("clarify", "currency"), (currency.kind, currency.note))

    def test_long_sentences_and_empty_text_are_unsupported(self):
        self.assertEqual("unsupported", self.kinds(""))
        self.assertEqual("unsupported", self.kinds(
            "please could you maybe explain how olx works for foreigners in bosnia"))

    def test_search_text_cannot_carry_like_wildcards(self):
        self.assertEqual("golf", parse_question("% _ golf under 9000").filters.search)

    def test_replies_refine_the_answer_they_reply_to(self):
        context = context_from_reply(BOT_ANSWER)
        self.assertEqual(Filters("Golf", 15000), context.filters)
        self.assertEqual(Filters("Golf", 15000, 2018), parse_question("from 2018", context).filters)
        self.assertEqual(Filters("Golf", 12000), parse_question("under 12000", context).filters)
        self.assertEqual(Filters("passat", 15000), parse_question("what about passat", context).filters)
        self.assertEqual("ignore", self.kinds("thanks", context))
        self.assertEqual("unsupported", self.kinds("30 days", context))

    def test_trend_follow_up_changes_only_the_period(self):
        context = context_from_reply("Observed asking-price changes · last 7 days\nSample: 4")
        followup = parse_question("and 30 days?", context)
        self.assertEqual(("trends", 30), (followup.kind, followup.days))

    def test_phase_one_budget_answers_remain_valid_context(self):
        context = context_from_reply("Recently observed at or below 25.000 KM · Golf\nShowing 3")
        self.assertEqual(Filters("Golf", 25000), context.filters)

    def test_unrelated_bot_messages_give_no_context(self):
        self.assertIsNone(context_from_reply("Car Analyzer · PARTIAL\nLatest attempt"))
        self.assertIsNone(context_from_reply(""))


class HandlerTests(unittest.TestCase):
    def run_update(self, update, **patches):
        call = Mock()
        with patch("telegram_bot.answer_intent", return_value=("answer", None)) as answer, \
                patch("telegram_bot.render_command", return_value="command answer") as render:
            result = process_update(update, Mock(), CONFIG, call=call)
        return result, call, answer, render

    def test_unauthorized_questions_get_no_reply_and_touch_no_data(self):
        for chat, user in [(-100999, 123), (CONFIG.chat_id, 999)]:
            result, call, answer, render = self.run_update(
                message("@mojagaraza_bot golf under 9000 KM", user=user, chat=chat))
            self.assertEqual("unauthorized", result["reason"])
            call.assert_not_called()
            answer.assert_not_called()
            render.assert_not_called()

    def test_unauthorized_button_callback_is_ignored(self):
        update = {"update_id": 5, "callback_query": {
            "id": "x", "from": {"id": 999}, "data": "f:9000:0:0:golf",
            "message": {"chat": {"id": CONFIG.chat_id}}}}
        result, call, answer, _ = self.run_update(update)
        self.assertEqual("unauthorized", result["reason"])
        call.assert_not_called()
        answer.assert_not_called()

    def test_unaddressed_group_chatter_is_ignored_even_from_allowed_users(self):
        result, call, answer, _ = self.run_update(message("golf under 9000 KM"))
        self.assertEqual("not_addressed", result["reason"])
        call.assert_not_called()
        answer.assert_not_called()

    def test_mention_reply_and_ask_command_are_answered(self):
        for update in (
            message("@mojagaraza_bot golf under 9000 KM"),
            message("golf under 9000 KM", reply=bot_reply()),
            message("/ask golf under 9000 KM"),
        ):
            result, call, answer, _ = self.run_update(update)
            self.assertTrue(result["handled"], update)
            answer.assert_called_once()
            self.assertEqual(CONFIG.chat_id, call.call_args.args[2]["chat_id"])

    def test_username_config_rejects_mentions_of_other_bots_and_people(self):
        config = TelegramConfig.from_env({**ENV, "TELEGRAM_BOT_USERNAME": "@mojagaraza_bot"})
        call = Mock()
        with patch("telegram_bot.answer_intent", return_value=("answer", None)):
            for body in ("@otherbot golf under 9000 KM", "@friend golf under 9000 KM"):
                result = process_update(message(body), Mock(), config, call=call)
                self.assertEqual("not_addressed", result["reason"])
            foreign_reply = {"from": {"id": 5, "is_bot": True, "username": "otherbot"}, "text": BOT_ANSWER}
            result = process_update(message("under 9000", reply=foreign_reply), Mock(), config, call=call)
            self.assertEqual("not_addressed", result["reason"])
            ok = process_update(message("@MojaGaraza_bot golf under 9000 KM"), Mock(), config, call=call)
        self.assertTrue(ok["handled"])

    def test_context_comes_only_from_a_bot_message_in_the_same_update(self):
        captured = []

        def fake_answer(engine, intent, config):
            captured.append(intent)
            return "answer", None

        human_reply = {"from": {"id": 456, "is_bot": False}, "text": BOT_ANSWER}
        with patch("telegram_bot.answer_intent", side_effect=fake_answer):
            process_update(message("/ask from 2018", reply=human_reply), Mock(), CONFIG, call=Mock())
            process_update(message("/ask from 2018", reply=bot_reply(), uid=11), Mock(), CONFIG, call=Mock())
            process_update(message("/ask from 2018", uid=12), Mock(), CONFIG, call=Mock())
        self.assertEqual(Filters("", None, 2018), captured[0].filters)
        self.assertEqual(Filters("Golf", 15000, 2018), captured[1].filters)
        self.assertEqual(Filters("", None, 2018), captured[2].filters)

    def test_acknowledgements_and_non_text_updates_get_no_answer(self):
        for update in (message("@mojagaraza_bot thanks"), message("", uid=20),
                       {"update_id": 21, "message": {"chat": {"id": CONFIG.chat_id},
                                                      "from": {"id": 123}, "photo": []}}):
            result, call, _, _ = self.run_update(update)
            self.assertFalse(result["handled"])
            call.assert_not_called()

    def test_database_failure_returns_generic_notice_without_internals(self):
        call = Mock()
        error = OperationalError("SELECT secret_table", {}, Exception("password=hunter2 host=db.internal"))
        with patch("telegram_bot.status_message", side_effect=error):
            result = process_update(message("/status"), Mock(), CONFIG, call=call)
        self.assertTrue(result["handled"])
        sent = call.call_args.args[2]["text"]
        self.assertEqual(DB_UNAVAILABLE, sent)
        for leaked in ("hunter2", "db.internal", "SELECT", "secret_table", CONFIG.bot_token):
            self.assertNotIn(leaked, sent)

    def test_existing_commands_still_route_to_their_handlers(self):
        for body, command in [
            ("/status", "status"), ("/latest", "latest"), ("/trends 7", "trends"),
            ("/budget 25000 Golf", "budget"), ("/compare Golf | Octavia", "compare"),
            ("/help", "help"), ("/start", "start"), ("/status@mojagaraza_bot", "status"),
        ]:
            result, call, answer, render = self.run_update(message(body))
            self.assertEqual(command, result["command"], body)
            render.assert_called_once()
            answer.assert_not_called()
            self.assertEqual("command answer", call.call_args.args[2]["text"])

    def test_existing_buttons_still_work_and_refine_buttons_run_a_find(self):
        for data, command in (("status", "status"), ("latest", "latest"), ("trends:30", "trends")):
            update = {"update_id": 6, "callback_query": {
                "id": "c", "from": {"id": 123}, "data": data,
                "message": {"chat": {"id": CONFIG.chat_id}}}}
            result, call, _, render = self.run_update(update)
            self.assertEqual(command, result["command"])
            render.assert_called_once()
            self.assertEqual("answerCallbackQuery", call.call_args_list[0].args[1])
        update = {"update_id": 7, "callback_query": {
            "id": "c", "from": {"id": 123}, "data": "f:13500:2016:0:golf",
            "message": {"chat": {"id": CONFIG.chat_id}}}}
        captured = []
        with patch("telegram_bot.answer_intent",
                   side_effect=lambda e, i, c: captured.append(i) or ("answer", i.filters)):
            result = process_update(update, Mock(), CONFIG, call=Mock())
        self.assertEqual(("find", Filters("golf", 13500, 2016)), (result["command"], captured[0].filters))

    def test_refine_callbacks_are_validated_and_fit_telegram_limits(self):
        filters = Filters("golf", 13500, 2016, 120000)
        data = refine_callback(filters)
        self.assertLessEqual(len(data.encode()), 64)
        self.assertEqual(filters, parse_refine_callback(data))
        self.assertIsNone(refine_callback(Filters("x" * 60, 9000)))
        for bad in ("f:abc:0:0:golf", "f:5:0:0:golf", "f:0:0:0:", "f:9000", "x:9000:0:0:golf",
                    "f:9000:3000:0:golf", "f:9999999:0:0:golf"):
            self.assertIsNone(parse_refine_callback(bad), bad)

    def test_older_five_field_buttons_still_work_and_offsets_are_bounded(self):
        self.assertEqual((Filters("golf", 13500, 2016), 0), telegram_bot.parse_refine_page("f:13500:2016:0:golf"))
        self.assertEqual((Filters("golf", 13500, 2016), 10), telegram_bot.parse_refine_page("f:13500:2016:0:10:golf"))
        for bad in ("f:13500:0:0:-5:golf", "f:13500:0:0:96:golf", "f:13500:0:0:x:golf",
                    "f:1:2:3:4:5:golf", "f:0:0:0:5:"):
            self.assertIsNone(telegram_bot.parse_refine_page(bad), bad)
        data = refine_callback(Filters("golf", 9000), 95)
        self.assertLessEqual(len(data.encode()), 64)

    def test_show_more_button_runs_a_find_at_that_offset(self):
        update = {"update_id": 8, "callback_query": {
            "id": "c", "from": {"id": 123}, "data": "f:15000:0:0:5:golf",
            "message": {"chat": {"id": CONFIG.chat_id}}}}
        captured = []
        with patch("telegram_bot.answer_intent",
                   side_effect=lambda e, i, c: captured.append(i) or ("answer", None)):
            process_update(update, Mock(), CONFIG, call=Mock())
        self.assertEqual((Filters("golf", 15000), 5), (captured[0].filters, captured[0].offset))

    def test_find_answers_offer_budget_buttons_others_do_not(self):
        buttons = keyboard(CONFIG, Filters("golf", 15000))["inline_keyboard"]
        first_row = [b["callback_data"] for b in buttons[0]]
        self.assertEqual(["f:13500:0:0:0:golf", "f:16500:0:0:0:golf"], first_row)
        plain = keyboard(CONFIG)["inline_keyboard"]
        self.assertEqual(2, len(plain))


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class QuestionQueryTests(unittest.TestCase):
    PREFIX = "__tg_"

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.addCleanup(self.cleanup)
        self.cleanup()

    def cleanup(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM listing_snapshots WHERE listing_id LIKE '__tg_%'"))
            c.execute(text("DELETE FROM dealer_vehicle_labels WHERE listing_id LIKE '__tg_%'"))
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__tg_%'"))
            c.execute(text("DELETE FROM website_collection_runs WHERE session_id LIKE '__tg_%'"))

    def seed(self, *, observed_hours_ago=1, run_status="completed"):
        rows = [
            ("__tg_1", "Volkswagen", "Golf", 2016, 12000, 150000, 1),
            ("__tg_2", "Volkswagen", "Golf", 2012, 8000, None, 2),
            ("__tg_3", "Škoda", "Octavia", 2018, 17000, 90000, 1),
            ("__tg_4", "Volkswagen", "Golf", 2019, 22000, 60000, 3),
            ("__tg_5", "Škoda", "Octavia", 2020, 0, 30000, 1),
            ("__tg_6", "Volkswagen", "Golf", 2017, 9000, 100000, 120),
        ]
        with self.engine.begin() as c:
            for lid, make, model, year, price, mileage, days in rows:
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,
                    mileage,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,:status,:mileage,
                    'olx.ba','olx_api',:seen,:seen,true,0)"""),
                    {"id": lid, "make": make, "model": model, "year": year, "price": price,
                     "status": "priced" if price else "missing", "mileage": mileage,
                     "seen": self.now - timedelta(days=days)})
            c.execute(text("""INSERT INTO listing_snapshots(listing_id,observed_at,asking_price,
                price_status,source_url,title,is_active) VALUES ('__tg_1',:seen,12000,'priced',
                'https://olx.ba/artikal/__tg_1','t',true)"""),
                {"seen": self.now - timedelta(hours=observed_hours_ago)})
            c.execute(text("""INSERT INTO website_collection_runs(session_id,started_at,finished_at,status,
                source_query,search_limit,detail_limit,search_attempted,detail_attempted,
                discovery_complete,details_completed,observations,new_listings,errors)
                VALUES ('__tg_run',:started,:finished,:status,'q',3,20,1,2,1,2,10,3,0)"""),
                {"started": self.now - timedelta(hours=observed_hours_ago + 1),
                 "finished": self.now - timedelta(hours=observed_hours_ago), "status": run_status})

    def counts(self):
        with self.engine.connect() as c:
            return tuple(c.execute(text(f"SELECT count(*) FROM {table}")).scalar()
                         for table in ("car_listings", "listing_snapshots", "website_collection_runs"))

    def test_find_applies_every_filter_and_reports_the_total(self):
        self.seed()
        reply = find_message(self.engine, Filters("golf", 15000, 2015, 200000))
        self.assertTrue(reply.startswith("Observed listings · golf · up to 15.000 KM · from 2015 · up to 200.000 km"))
        self.assertIn("Showing 1 of 1", reply)
        self.assertIn("__tg_1", reply)
        for excluded in ("__tg_2", "__tg_3", "__tg_4", "__tg_5", "__tg_6"):
            self.assertNotIn(excluded, reply)
        self.assertNotIn("Stale data", reply)
        self.assertIn("not recommendations", reply)

    def test_listings_missing_mileage_or_price_never_match_those_filters(self):
        self.seed()
        reply = find_message(self.engine, Filters("golf", None, None, 200000))
        self.assertNotIn("__tg_2", reply)  # unknown mileage
        self.assertIn("Showing 1–2 of 2", reply)
        self.assertIn("without a recorded year or mileage", reply)
        octavia = find_message(self.engine, Filters("octavia"))
        self.assertNotIn("__tg_5", octavia)  # no price

    def test_accent_insensitive_and_wildcard_safe_matching(self):
        self.seed()
        self.assertIn("__tg_3", find_message(self.engine, Filters("skoda")))
        self.assertIn("__tg_3", find_message(self.engine, Filters("Škoda Octavia")))
        for wildcard in ("%", "_", "g_lf", "%%"):
            reply = find_message(self.engine, Filters(wildcard))
            self.assertIn("No matching listings", reply, wildcard)

    def test_hostile_text_is_data_and_never_changes_the_database(self):
        self.seed()
        before = self.counts()
        for hostile in ("golf'; DROP TABLE car_listings; --", "1) OR (1=1", "golf\\"):
            intent = parse_question(hostile)
            if intent.kind == "find":
                find_message(self.engine, intent.filters)
        for label in ("'; DELETE FROM car_listings; --", "golf' OR '1'='1"):
            model_stats_message(self.engine, label)
            compare_message(self.engine, f"{label} | octavia")
        self.assertEqual(before, self.counts())

    def test_empty_database_is_stated_not_papered_over(self):
        reply = find_message(self.engine, Filters("golf", 15000))
        self.assertIn("No matching listings", reply)
        self.assertIn("no stored observations", reply)
        self.assertIn("no stored observations", model_stats_message(self.engine, "golf"))

    def test_stale_and_partial_data_are_flagged(self):
        self.seed(observed_hours_ago=24 * 6, run_status="partial")
        reply = find_message(self.engine, Filters("golf"))
        self.assertIn("Stale data", reply)
        self.assertIn("6 days old", reply)
        self.assertIn("PARTIAL", reply)

    def test_model_stats_and_thin_sample_warning(self):
        self.seed()
        reply = model_stats_message(self.engine, "Volkswagen Golf")
        self.assertIn("Sample: 3 listings · 3 priced", reply)
        self.assertIn("Median asking: 12.000 KM", reply)
        self.assertIn("Thin sample", reply)
        self.assertIn("No matching listings", model_stats_message(self.engine, "ferrari"))

    def test_compare_is_accent_insensitive_and_flags_thin_samples(self):
        self.seed()
        reply = compare_message(self.engine, "golf | skoda octavia")
        self.assertIn("Sample: 3 listings", reply)
        self.assertIn("Sample: 2 listings · 1 priced", reply)
        self.assertEqual(2, reply.count("Thin sample"))

    def seed_many(self):
        rows = [  # id, year, mileage, price
            ("__tg_p1", 2015, 120000, 9000), ("__tg_p2", 2020, 90000, 14000),
            ("__tg_p3", 2020, 30000, 14500), ("__tg_p4", 2018, 50000, 11000),
            ("__tg_p5", 2018, None, 10000), ("__tg_p6", 2012, 200000, 5000),
            ("__tg_p7", 2019, 70000, 13000),
        ]
        with self.engine.begin() as c:
            for lid, year, mileage, price in rows:
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,
                    mileage,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,'Volkswagen','Golf',:year,:price,'priced',:mileage,
                    'olx.ba','olx_api',:seen,:seen,true,0)"""),
                    {"id": lid, "year": year, "price": price, "mileage": mileage,
                     "seen": self.now - timedelta(days=1)})

    def test_results_are_ranked_newest_year_then_lowest_mileage(self):
        self.seed_many()
        first = find_message(self.engine, Filters("golf", 15000))
        order = [first.index(f"__tg_p{n}") for n in (3, 2, 7, 4, 5)]
        self.assertEqual(sorted(order), order)  # p3(2020,30k) p2(2020,90k) p7(2019) p4(2018,50k) p5(2018,?)
        self.assertIn("Showing 1–5 of 7", first)
        self.assertIn("newest model year first", first)
        self.assertNotIn("__tg_p1", first)

    def test_show_more_pages_through_every_match_exactly_once(self):
        self.seed_many()
        second = find_message(self.engine, Filters("golf", 15000), 5)
        self.assertIn("Showing 6–7 of 7", second)
        for lid in ("__tg_p1", "__tg_p6"):
            self.assertIn(lid, second)
        self.assertNotIn("__tg_p3", second)
        beyond = find_message(self.engine, Filters("golf", 15000), 95)
        self.assertIn("No more matches", beyond)
        self.assertTrue(beyond.startswith("Observed listings · golf · up to 15.000 KM"))

    def test_answer_intent_offers_show_more_only_when_more_exist(self):
        self.seed_many()
        text_value, page = telegram_bot.answer_intent(self.engine, Intent("find", Filters("golf", 15000)), CONFIG)
        self.assertEqual(5, page.next_offset)
        buttons = keyboard(CONFIG, page)["inline_keyboard"]
        self.assertEqual("Show more (6–10)", buttons[0][0]["text"])
        self.assertEqual("f:15000:0:0:5:golf", buttons[0][0]["callback_data"])
        text_value, last = telegram_bot.answer_intent(
            self.engine, Intent("find", Filters("golf", 15000), offset=5), CONFIG)
        self.assertIsNone(last.next_offset)
        self.assertFalse(any("Show more" in b["text"] for row in keyboard(CONFIG, last)["inline_keyboard"] for b in row))

    def test_existing_status_still_reads_a_seeded_run(self):
        self.seed()
        reply = status_message(self.engine)
        self.assertIn("COMPLETED", reply)
        self.assertIn("10 observations", reply)


if __name__ == "__main__":
    unittest.main()
