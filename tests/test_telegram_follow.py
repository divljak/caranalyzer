"""Follow a model from chat: same table and identity as the web Following page."""

from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text

import dealer_api
import telegram_bot
import telegram_follow
from telegram_bot import (
    Choices, TelegramConfig, answer_intent, follow_callback, parse_follow_callback, process_update,
)
from telegram_intent import Filters, Intent, parse_question
from telegram_llm import intent_from_plan

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)
MAKE, MODEL = "TgTest", "Golf"


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    for name in ("GEMINI_API_KEY", "TELEGRAM_DEFAULT_LANGUAGE"):
        os.environ.pop(name, None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def dm(text_value, *, user=123, chat_id=123, uid=1):
    return {"update_id": uid, "message": {"chat": {"id": chat_id, "type": "private"},
                                           "from": {"id": user}, "text": text_value}}


def press(data, *, user=123, chat_id=123, uid=1):
    return {"update_id": uid, "callback_query": {"id": "cb", "from": {"id": user}, "data": data,
                                                 "message": {"chat": {"id": chat_id, "type": "private"}}}}


def last_payload(call):
    return [c.args[2] for c in call.call_args_list if c.args[1] in {"sendMessage", "editMessageText"}][-1]


def buttons(payload):
    return [b for row in (payload.get("reply_markup") or {"inline_keyboard": []})["inline_keyboard"] for b in row]


class ParsingTests(unittest.TestCase):
    def kind(self, value):
        intent = parse_question(value)
        return intent.kind, intent.filters.search

    def test_follow_requests_in_both_languages(self):
        self.assertEqual(("follow", "golf"), self.kind("prati Golf"))
        self.assertEqual(("follow", "volkswagen golf"), self.kind("follow volkswagen golf"))
        self.assertEqual(("follow", "škoda octavia"), self.kind("zaprati Škoda Octavia"))
        self.assertEqual(("follow", "passat"), self.kind("track Passat"))

    def test_asking_what_is_followed_lists_the_follows(self):
        for value in ("what am I following", "šta pratim", "Show me what I'm following", "praćeni modeli", "following"):
            self.assertEqual("following", parse_question(value).kind, value)

    def test_other_questions_are_unaffected(self):
        self.assertEqual("find", parse_question("Golf under 15000 KM").kind)
        self.assertEqual("clarify", parse_question("what car should I buy").kind)

    def test_budgets_make_it_a_search_not_a_follow(self):
        self.assertEqual("following", parse_question("follow golf under 15000 KM").kind)

    def test_the_planner_can_ask_to_follow(self):
        self.assertEqual(("follow", "golf"), (lambda i: (i.kind, i.filters.search))(
            intent_from_plan({"intent": "follow", "search": "golf"})))
        self.assertEqual("clarify", intent_from_plan({"intent": "follow"}).kind)
        self.assertEqual("following", intent_from_plan({"intent": "following"}).kind)


class CallbackTests(unittest.TestCase):
    def test_follow_buttons_round_trip_and_validate(self):
        data = follow_callback("Škoda", "Octavia Combi")
        self.assertEqual(("Škoda", "Octavia Combi"), parse_follow_callback(data))
        self.assertLessEqual(len(data.encode()), 64)
        self.assertIsNone(follow_callback("x" * 40, "y" * 40))  # too long for Telegram
        for bad in ("p:", "p:onlymake", "p:a|b|c", "p:|model", "p:make|", "q:a|b", None, "p:a|b\x00c", "p:" + "a" * 90 + "|b"):
            self.assertIsNone(parse_follow_callback(bad), bad)


class FollowFlowTests(unittest.TestCase):
    """The bot asks first; only a tap on a confirm button changes anything."""

    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS):
            store.clear()
        self.call = Mock(return_value={"message_id": 1})
        self.engine = Mock()

    def candidates(self, *items):
        return patch("telegram_follow.resolve_models", return_value=[{"make": m, "model": n, "n": c} for m, n, c in items])

    def test_asking_to_follow_offers_confirm_buttons_and_saves_nothing(self):
        with self.candidates(("Volkswagen", "Golf", 120), ("Volkswagen", "Golf Plus", 8)), \
                patch("telegram_follow.follow_model") as follow:
            process_update(dm("prati golf"), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        follow.assert_not_called()
        payload = last_payload(self.call)
        self.assertIn("Koji model da pratim?", payload["text"])
        self.assertEqual(["✅ Prati Volkswagen Golf", "✅ Prati Volkswagen Golf Plus"], [b["text"] for b in buttons(payload)])
        self.assertEqual("p:Volkswagen|Golf", buttons(payload)[0]["callback_data"])

    def test_a_single_match_asks_for_one_confirmation_in_english(self):
        with self.candidates(("Volkswagen", "Golf", 120)):
            process_update(dm("follow golf"), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        payload = last_payload(self.call)
        self.assertEqual("Follow Volkswagen Golf? Following shows on the web Following page too.", payload["text"])
        self.assertEqual(1, len(buttons(payload)))

    def test_unknown_models_are_said_plainly(self):
        with self.candidates():
            process_update(dm("follow zzzz"), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        self.assertIn("couldn't find a model like “zzzz”", last_payload(self.call)["text"])
        self.assertEqual([], buttons(last_payload(self.call)))

    def test_tapping_confirm_follows_and_offers_unfollow(self):
        with patch("telegram_follow.follow_model", return_value=("a" * 32, True)) as follow:
            process_update(press("p:Volkswagen|Golf"), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        follow.assert_called_once_with(self.engine, "Volkswagen", "Golf")
        payload = last_payload(self.call)
        self.assertIn("✅ Now following Volkswagen Golf", payload["text"])
        self.assertEqual(["u:" + "a" * 32, "following"], [b["callback_data"] for b in buttons(payload)])

    def test_following_again_is_reported_not_duplicated(self):
        with patch("telegram_follow.follow_model", return_value=("b" * 32, False)):
            process_update(press("p:Volkswagen|Golf"), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        self.assertIn("already following Volkswagen Golf", last_payload(self.call)["text"])

    def test_a_forged_or_stale_button_follows_nothing(self):
        with patch("telegram_follow.follow_model", return_value=None):
            process_update(press("p:Nope|Nothing"), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        self.assertIn("couldn't find a model like", last_payload(self.call)["text"])
        for bad in ("p:a|b|c", "p:", "u:notanid"):
            with patch("telegram_follow.follow_model") as follow:
                process_update(press(bad, uid=2), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
            follow.assert_not_called()

    def test_unfollow_and_list_buttons(self):
        with patch("telegram_follow.unfollow", return_value="Volkswagen Golf") as unfollow:
            process_update(press("u:" + "a" * 32), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        unfollow.assert_called_once_with(self.engine, "a" * 32)
        self.assertIn("Stopped following Volkswagen Golf.", last_payload(self.call)["text"])
        with patch("telegram_follow.unfollow", return_value=None):
            process_update(press("u:" + "b" * 32, uid=2), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        self.assertIn("no longer on your list", last_payload(self.call)["text"])
        follows = [("a" * 32, "Volkswagen Golf"), ("b" * 32, "Škoda Octavia")]
        with patch("telegram_follow.following_message", return_value=("Followed models\n• ...", follows)):
            process_update(press("following", uid=3), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        self.assertEqual(["u:" + "a" * 32, "u:" + "b" * 32], [b["callback_data"] for b in buttons(last_payload(self.call))])

    def test_commands(self):
        with self.candidates(("Volkswagen", "Golf", 5)):
            process_update(dm("/follow golf"), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        self.assertEqual("p:Volkswagen|Golf", buttons(last_payload(self.call))[0]["callback_data"])
        with patch("telegram_follow.following_message", return_value=("list", [])) as listing:
            process_update(dm("/following", uid=2), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
            process_update(dm("/follow", uid=3), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        self.assertEqual(2, listing.call_count)

    def test_strangers_cannot_follow_anything(self):
        with patch("telegram_follow.follow_model") as follow, patch("telegram_follow.unfollow") as unfollow, \
                patch("telegram_follow.resolve_models") as resolve:
            for update in (press("p:Volkswagen|Golf", user=999, chat_id=999), press("u:" + "a" * 32, user=999, chat_id=999),
                           dm("/follow golf", user=999, chat_id=999), press("p:Volkswagen|Golf", user=123, chat_id=999)):
                result = process_update(update, self.engine, CONFIG, call=self.call, llm=None, phraser=None)
                self.assertEqual("unauthorized", result["reason"])
        for mock in (follow, unfollow, resolve):
            mock.assert_not_called()
        self.call.assert_not_called()

    def test_following_is_never_rephrased_by_gemini(self):
        phraser = Mock(return_value=("chatty", False))
        with self.candidates(("Volkswagen", "Golf", 5)):
            process_update(dm("prati golf"), self.engine, CONFIG, call=self.call, llm=None, phraser=phraser)
        phraser.assert_not_called()

    def test_a_database_failure_while_saving_is_reported_gently(self):
        from sqlalchemy.exc import OperationalError
        with patch("telegram_follow.follow_model", side_effect=OperationalError("x", {}, Exception("secret"))):
            process_update(press("p:Volkswagen|Golf"), self.engine, CONFIG, call=self.call, llm=None, phraser=None)
        payload = last_payload(self.call)
        self.assertIn("couldn't save that", payload["text"])
        self.assertNotIn("secret", payload["text"])


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class FollowDatabaseTests(unittest.TestCase):
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
            c.execute(text("DELETE FROM dealer_model_searches WHERE filters->>'make' ILIKE 'TgTest%'"))
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__tg_%'"))
            c.execute(text("DELETE FROM scraping_logs WHERE session_id LIKE '__tg_%'"))

    def seed(self, clean_anchor=True):
        rows = [("__tg_f1", MAKE, "Golf", 2016, 12000, 2), ("__tg_f2", MAKE, "Golf", 2015, 9000, 3),
                ("__tg_f3", MAKE, "Golf", 2018, 15000, 1), ("__tg_f4", "TgTest Škoda", "Octavia", 2018, 17000, 1),
                ("__tg_f5", MAKE, "Passat", 2017, 14000, 1), ("__tg_f6", MAKE, "Golf", 2014, 8000, 120)]
        with self.engine.begin() as c:
            for lid, make, model, year, price, days in rows:
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,source,
                    collection_method,last_verified_at,first_seen_at,is_active,consecutive_missing_runs)
                    VALUES (:id,:make,:model,:year,:price,'priced',100000,'olx.ba','olx_api',:seen,:seen,true,0)"""),
                    {"id": lid, "make": make, "model": model, "year": year, "price": price,
                     "seen": self.now - timedelta(days=days)})
            if clean_anchor:
                c.execute(text("""INSERT INTO scraping_logs(session_id,start_time,end_time,status,errors_count)
                    VALUES ('__tg_log',:a,:b,'completed',0)"""),
                    {"a": self.now - timedelta(hours=2), "b": self.now - timedelta(hours=1)})

    def rows(self):
        with self.engine.connect() as c:
            return c.execute(text("SELECT id,fingerprint,filters,baseline FROM dealer_model_searches "
                                  "WHERE filters->>'make' ILIKE 'TgTest%'")).mappings().all()

    def test_models_are_resolved_the_way_the_web_identifies_them(self):
        self.seed()
        found = telegram_follow.resolve_models(self.engine, "golf")
        self.assertEqual([("TgTest", "Golf", 3)], [(r["make"], r["model"], r["n"]) for r in found])  # 120-day-old one excluded
        self.assertEqual(["Octavia"], [r["model"] for r in telegram_follow.resolve_models(self.engine, "skoda octavia")])
        self.assertEqual({"Golf", "Passat"}, {r["model"] for r in telegram_follow.resolve_models(self.engine, "tgtest")
                                              if r["make"] == "TgTest"})
        self.assertEqual(["Golf"], [r["model"] for r in telegram_follow.resolve_models(self.engine, "tgtest golf")])

    def test_wildcards_and_junk_resolve_to_nothing(self):
        self.seed()
        for search in ("%", "_", "g_lf", "", "   ", "'; DROP TABLE car_listings; --"):
            self.assertEqual([], telegram_follow.resolve_models(self.engine, search), search)
        self.assertGreater(self.engine.connect().execute(text("SELECT count(*) FROM car_listings")).scalar(), 0)

    def test_following_from_chat_is_identical_to_following_on_the_web(self):
        self.seed()
        created = telegram_follow.follow_model(self.engine, MAKE, MODEL)
        self.assertTrue(created[1])
        with patch.object(dealer_api, "engine", self.engine):
            web = dealer_api.save_model_search(dealer_api.Filters(make=MAKE, model=MODEL))
        self.assertEqual(created[0], web["id"])  # same fingerprint, so the web call found the bot's row
        self.assertEqual(1, len(self.rows()))
        row = self.rows()[0]
        self.assertEqual(MAKE, row["filters"]["make"])
        self.assertIsNone(row["filters"]["price_max"])
        self.assertEqual(MODEL, row["baseline"]["model"])
        self.assertEqual(3, row["baseline"]["sample_size"])  # the web evidence was stored as the baseline

    def test_following_twice_does_not_duplicate(self):
        self.seed()
        first = telegram_follow.follow_model(self.engine, MAKE, MODEL)
        second = telegram_follow.follow_model(self.engine, MAKE.lower(), MODEL)
        self.assertEqual((first[0], False), second)  # a different spelling is the same model
        self.assertEqual(1, len(self.rows()))
        self.assertEqual(MAKE, self.rows()[0]["filters"]["make"])  # saved as stored, not as typed

    def test_unknown_models_are_never_saved(self):
        self.seed()
        for make, model in (("TgTest", "Nope"), ("Nope", "Golf"), ("", "Golf"), ("TgTest", ""), ("x" * 200, "Golf")):
            self.assertIsNone(telegram_follow.follow_model(self.engine, make, model))
        self.assertEqual([], self.rows())

    def test_without_a_clean_collection_the_follow_is_still_saved(self):
        self.seed(clean_anchor=False)
        with self.engine.connect() as c:
            other_clean = c.execute(text("SELECT count(*) FROM scraping_logs WHERE status='completed' "
                                         "AND errors_count=0 AND session_id NOT LIKE '__tg_%'")).scalar()
        result = telegram_follow.follow_model(self.engine, MAKE, MODEL)
        self.assertTrue(result[1])
        if not other_clean:
            self.assertEqual({"make": MAKE, "model": MODEL}, self.rows()[0]["baseline"])

    def test_unfollow(self):
        self.seed()
        search_id, _ = telegram_follow.follow_model(self.engine, MAKE, MODEL)
        for bad in ("", "short", "z" * 32, search_id.upper(), None, 5, search_id + "x"):
            self.assertIsNone(telegram_follow.unfollow(self.engine, bad), bad)
        self.assertEqual(1, len(self.rows()))
        self.assertEqual("TgTest Golf", telegram_follow.unfollow(self.engine, search_id))
        self.assertEqual([], self.rows())
        self.assertIsNone(telegram_follow.unfollow(self.engine, search_id))

    def test_the_follow_list_shows_what_is_new_since_following(self):
        self.seed()
        telegram_follow.follow_model(self.engine, MAKE, MODEL)
        message, follows = telegram_follow.following_message(self.engine, "en")
        self.assertIn("Followed models", message)
        self.assertIn("• TgTest Golf · 3 listings now · median 12.000 KM · 0 first seen since you followed", message)
        self.assertEqual("TgTest Golf", follows[0][1])
        with self.engine.begin() as c:
            c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,source,
                collection_method,last_verified_at,first_seen_at,is_active,consecutive_missing_runs)
                VALUES ('__tg_f9',:make,'Golf',2019,16000,'priced',50000,'olx.ba','olx_api',:seen,:seen,true,0)"""),
                {"make": MAKE, "seen": datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=5)})
        message, _ = telegram_follow.following_message(self.engine, "bs")
        self.assertIn("• TgTest Golf · trenutno 4 oglasa", message)
        self.assertIn("1 prvi put viđeno otkad pratiš", message)
        self.assertIn("Lista je zajednička", message)

    def test_an_empty_list_and_a_model_with_no_recent_listings(self):
        self.assertIn("not following any model yet", telegram_follow.following_message(self.engine, "en")[0])
        self.assertIn("još ne pratiš nijedan model", telegram_follow.following_message(self.engine, "bs")[0].lower())
        self.seed()
        telegram_follow.follow_model(self.engine, MAKE, MODEL)
        with self.engine.begin() as c:
            c.execute(text("UPDATE car_listings SET last_verified_at = last_verified_at - interval '200 days' "
                           "WHERE listing_id LIKE '__tg_f%'"))
        self.assertIn("no listings seen in the last 90 days", telegram_follow.following_message(self.engine, "en")[0])

    def test_resolving_and_listing_never_change_the_database(self):
        self.seed()
        with self.engine.connect() as c:
            before = c.execute(text("SELECT (SELECT count(*) FROM car_listings),"
                                    "(SELECT count(*) FROM dealer_model_searches)")).one()
        telegram_follow.resolve_models(self.engine, "golf")
        telegram_follow.following_message(self.engine, "en")
        with self.engine.connect() as c:
            after = c.execute(text("SELECT (SELECT count(*) FROM car_listings),"
                                   "(SELECT count(*) FROM dealer_model_searches)")).one()
        self.assertEqual(tuple(before), tuple(after))


if __name__ == "__main__":
    unittest.main()
