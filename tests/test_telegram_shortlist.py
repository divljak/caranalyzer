"""Save cars to the shortlist from chat: least privilege, never overwrite the web's work."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError

import telegram_bot
import telegram_shortlist as sl
from telegram_bot import Page, TelegramConfig, keyboard, process_update, save_callback
from telegram_intent import Filters, parse_question
from telegram_llm import intent_from_plan

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)
SQL_DIR = Path(__file__).resolve().parent.parent / "docs" / "proposed-migrations"


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


class ButtonTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS):
            store.clear()

    def test_star_buttons_sit_under_the_results_two_to_a_row(self):
        items = tuple((f"id{n}", 2016 + n % 3, 10000 + n * 500) for n in range(5))
        rows = keyboard(CONFIG, Page(Filters("golf", 15000), 5, items=items))["inline_keyboard"]
        self.assertEqual([2, 2, 1], [len(row) for row in rows[:3]])
        self.assertEqual("⭐ 2016 · 10.000 KM", rows[0][0]["text"])
        self.assertEqual("c:id0", rows[0][0]["callback_data"])
        self.assertTrue(all("⭐" not in b["text"] for row in rows[3:] for b in row))  # refinements follow
        self.assertIn("Show more (6–10)", [b["text"] for row in rows for b in row])

    def test_no_star_buttons_when_nothing_is_shown_or_the_id_is_unsafe(self):
        no_items = keyboard(CONFIG, Page(Filters("golf", 15000), 5))["inline_keyboard"]
        self.assertFalse(any("⭐" in b["text"] for row in no_items for b in row))
        unsafe = (("bad id with spaces", 2016, 9000), ("x" * 80, 2016, 9000), ("good_1", 2016, 9000))
        rows = keyboard(CONFIG, Page(Filters("golf", 15000), items=unsafe))["inline_keyboard"]
        stars = [b for row in rows for b in row if "⭐" in b["text"]]
        self.assertEqual(["c:good_1"], [b["callback_data"] for b in stars][:1])
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for b in stars))
        self.assertIsNone(save_callback("y" * 80))

    def test_star_labels_in_bosnian_and_without_a_price(self):
        rows = keyboard(CONFIG, Page(Filters("golf", 15000), items=(("a1", 2018, None), ("a2", None, 9000))), "bs")
        labels = [b["text"] for row in rows["inline_keyboard"] for b in row if b["text"].startswith("⭐")]
        self.assertEqual(["⭐ 2018", "⭐ 9.000 KM"], labels)

    def test_a_chat_reply_that_hides_the_listings_hides_the_stars_too(self):
        call = Mock(return_value={"message_id": 1})
        page = Page(Filters("golf", 15000), 5, 2012, 250000, (("id1", 2016, 9000),))
        with patch("telegram_bot.answer_intent", return_value=("Rezultati", page)):
            process_update(dm("Golf do 15000 KM"), Mock(), CONFIG, call=call, llm=None,
                           phraser=Mock(return_value=("Našao sam ih. Hoćeš da vidiš?", False)))
        self.assertFalse(any("⭐" in b["text"] for b in buttons(last_payload(call))))
        call = Mock(return_value={"message_id": 1})
        with patch("telegram_bot.answer_intent", return_value=("Rezultati", page)):
            process_update(dm("Golf do 15000 KM", uid=2), Mock(), CONFIG, call=call, llm=None,
                           phraser=Mock(return_value=("Evo.", True)))
        self.assertTrue(any("⭐" in b["text"] for b in buttons(last_payload(call))))


class ParsingTests(unittest.TestCase):
    def test_asking_for_the_shortlist(self):
        for value in ("show my saved cars", "my shortlist", "šta sam sačuvao", "sačuvani auti", "moja lista", "shortlist"):
            self.assertEqual("shortlist", parse_question(value).kind, value)

    def test_other_requests_are_unaffected(self):
        self.assertEqual("find", parse_question("Golf under 15000 KM").kind)
        self.assertEqual("follow", parse_question("prati Golf").kind)
        self.assertEqual("following", parse_question("šta pratim").kind)
        self.assertEqual("clarify", parse_question("what car should I buy").kind)

    def test_the_planner_can_ask_for_the_shortlist(self):
        self.assertEqual("shortlist", intent_from_plan({"intent": "shortlist"}).kind)


class FlowTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS):
            store.clear()
        self.call = Mock(return_value={"message_id": 1})
        self.row = {"listing_id": "80138618", "make": "Volkswagen", "model": "Golf", "year": 2016, "price": 12000}

    def run_update(self, update):
        return process_update(update, Mock(), CONFIG, call=self.call, llm=None, phraser=None)

    def test_tapping_a_star_saves_and_offers_undo(self):
        with patch("telegram_shortlist.save_listing", return_value=(self.row, True)) as save:
            self.run_update(press("c:80138618"))
        save.assert_called_once()
        self.assertEqual("80138618", save.call_args.args[1])
        payload = last_payload(self.call)
        self.assertEqual("⭐ Saved to your shortlist: Volkswagen Golf · 2016 · 12.000 KM", payload["text"])
        self.assertEqual(["x:80138618", "shortlist"], [b["callback_data"] for b in buttons(payload)])

    def test_saving_twice_says_so(self):
        with patch("telegram_shortlist.save_listing", return_value=(self.row, False)):
            self.run_update(press("c:80138618"))
        self.assertIn("is already on your shortlist", last_payload(self.call)["text"])

    def test_before_the_sql_is_applied_the_bot_explains_instead_of_failing(self):
        with patch("telegram_shortlist.save_listing", side_effect=sl.NotEnabled()):
            self.run_update(press("c:80138618", uid=1))
        self.assertIn("isn't switched on in the database yet", last_payload(self.call)["text"])
        self.assertEqual([], buttons(last_payload(self.call)))

    def test_other_database_problems_are_reported_gently(self):
        error = OperationalError("INSERT", {}, Exception("secret detail"))
        with patch("telegram_shortlist.save_listing", side_effect=error):
            self.run_update(press("c:80138618"))
        self.assertIn("couldn't save that", last_payload(self.call)["text"])
        self.assertNotIn("secret", last_payload(self.call)["text"])

    def test_unknown_listings_and_forged_buttons(self):
        with patch("telegram_shortlist.save_listing", return_value=None):
            self.run_update(press("c:99999999"))
        self.assertIn("couldn't find that listing", last_payload(self.call)["text"])
        for bad in ("c:", "c:has space", "c:" + "z" * 80, "x:", "x:../etc"):
            with patch("telegram_shortlist.save_listing") as save, patch("telegram_shortlist.remove_listing") as remove:
                self.run_update(press(bad, uid=3))
            save.assert_not_called()
            remove.assert_not_called()

    def test_removal_outcomes(self):
        for outcome, expected in (("removed", "Removed from your shortlist"), ("kept", "status or note set on the web"),
                                  ("gone", "no longer on your shortlist")):
            with patch("telegram_shortlist.remove_listing", return_value=(self.row, outcome)):
                self.run_update(press("x:80138618"))
            self.assertIn(expected, last_payload(self.call)["text"])

    def test_listing_the_shortlist_by_command_and_by_asking(self):
        items = [("80138618", "Volkswagen Golf · 2016 · 12.000 KM")]
        with patch("telegram_shortlist.shortlist_message", return_value=("Your shortlist\n• ...", items)) as listing:
            self.run_update(dm("/shortlist", uid=1))
            self.run_update(dm("show my saved cars", uid=2))
            self.run_update(press("shortlist", uid=3))
        self.assertEqual(3, listing.call_count)
        self.assertEqual("x:80138618", buttons(last_payload(self.call))[0]["callback_data"])

    def test_strangers_cannot_save_or_remove_anything(self):
        with patch("telegram_shortlist.save_listing") as save, patch("telegram_shortlist.remove_listing") as remove, \
                patch("telegram_shortlist.shortlist_message") as listing:
            for update in (press("c:1", user=999, chat_id=999), press("x:1", user=999, chat_id=999),
                           dm("/shortlist", user=999, chat_id=999), press("c:1", user=123, chat_id=999)):
                self.assertEqual("unauthorized", self.run_update(update)["reason"])
        for mock in (save, remove, listing):
            mock.assert_not_called()
        self.call.assert_not_called()

    def test_gemini_never_rewrites_a_save_confirmation(self):
        phraser = Mock(return_value=("chatty", False))
        with patch("telegram_shortlist.shortlist_message", return_value=("list", [])):
            process_update(dm("my shortlist"), Mock(), CONFIG, call=self.call, llm=None, phraser=phraser)
        phraser.assert_not_called()


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class ShortlistDatabaseTests(unittest.TestCase):
    ROLE = "tg_shortlist_app"

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])
        with cls.engine.begin() as c:
            cls.rls_was_on = c.execute(text(
                "SELECT relrowsecurity FROM pg_class WHERE oid='public.dealer_candidates'::regclass")).scalar()

    @classmethod
    def tearDownClass(cls):
        cls.cleanup_role(cls)
        with cls.engine.begin() as c:
            c.execute(text("ALTER TABLE public.dealer_candidates " + ("ENABLE" if cls.rls_was_on else "DISABLE")
                           + " ROW LEVEL SECURITY"))
        cls.engine.dispose()

    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.addCleanup(self.cleanup)
        self.cleanup()

    def cleanup(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM dealer_candidates WHERE listing_id LIKE '__tg_%'"))
            c.execute(text("DELETE FROM listing_snapshots WHERE listing_id LIKE '__tg_%'"))
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__tg_%'"))

    def cleanup_role(self):
        with self.engine.begin() as c:
            if c.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": self.ROLE}).scalar():
                for table in ("dealer_candidates", "car_listings", "dealer_vehicle_labels", "listing_snapshots"):
                    c.execute(text(f"DROP POLICY IF EXISTS tg_read ON public.{table}"))
                c.execute(text(f"DROP OWNED BY {self.ROLE}"))
                c.execute(text(f"DROP ROLE {self.ROLE}"))

    def seed(self):
        rows = [("__tg_s1", "Volkswagen", "Golf", 2016, 12000, 1), ("__tg_s2", "Škoda", "Octavia", 2018, 17000, 30)]
        with self.engine.begin() as c:
            for lid, make, model, year, price, days in rows:
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,source,
                    collection_method,last_verified_at,first_seen_at,is_active,consecutive_missing_runs)
                    VALUES (:id,:make,:model,:year,:price,'priced',100000,'olx.ba','olx_api',:seen,:seen,true,0)"""),
                    {"id": lid, "make": make, "model": model, "year": year, "price": price,
                     "seen": self.now - timedelta(days=days)})

    def candidates(self):
        with self.engine.connect() as c:
            return c.execute(text("SELECT listing_id,status,note FROM dealer_candidates WHERE listing_id LIKE '__tg_%' "
                                  "ORDER BY listing_id")).mappings().all()

    def test_saving_adds_a_plain_shortlisted_row(self):
        self.seed()
        row, created = sl.save_listing(self.engine, "__tg_s1")
        self.assertTrue(created)
        self.assertEqual("Volkswagen Golf · 2016 · 12.000 KM", sl.label_for(row))
        self.assertEqual([("__tg_s1", "shortlisted", "")], [tuple(r.values()) for r in self.candidates()])

    def test_saving_never_overwrites_what_was_set_on_the_web(self):
        self.seed()
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO dealer_candidates(listing_id,status,note) VALUES ('__tg_s1','contacted','called seller')"))
        row, created = sl.save_listing(self.engine, "__tg_s1")
        self.assertFalse(created)
        self.assertEqual([("__tg_s1", "contacted", "called seller")], [tuple(r.values()) for r in self.candidates()])

    def test_unknown_or_unsafe_listings_are_never_saved(self):
        self.seed()
        for bad in ("__tg_nope", "", "has space", "x" * 80, None, "'; DROP TABLE dealer_candidates; --"):
            self.assertIsNone(sl.save_listing(self.engine, bad), bad)
        self.assertEqual([], self.candidates())

    def test_removal_only_removes_what_the_bot_added(self):
        self.seed()
        sl.save_listing(self.engine, "__tg_s1")
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO dealer_candidates(listing_id,status,note) VALUES ('__tg_s2','inspect','')"))
        self.assertEqual("kept", sl.remove_listing(self.engine, "__tg_s2")[1])
        with self.engine.begin() as c:
            c.execute(text("UPDATE dealer_candidates SET status='shortlisted', note='check rust' WHERE listing_id='__tg_s2'"))
        self.assertEqual("kept", sl.remove_listing(self.engine, "__tg_s2")[1])
        self.assertEqual("removed", sl.remove_listing(self.engine, "__tg_s1")[1])
        self.assertEqual("gone", sl.remove_listing(self.engine, "__tg_s1")[1])
        self.assertEqual("gone", sl.remove_listing(self.engine, "bad id")[1])
        self.assertEqual(["__tg_s2"], [r["listing_id"] for r in self.candidates()])

    def test_the_list_shows_status_price_changes_and_old_sightings(self):
        self.seed()
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO dealer_candidates(listing_id,status,note,saved_at) VALUES "
                           "('__tg_s1','contacted','',:saved),('__tg_s2','shortlisted','',:saved)"),
                      {"saved": self.now - timedelta(hours=1)})
            c.execute(text("""INSERT INTO listing_snapshots(listing_id,observed_at,asking_price,price_status,source_url,
                title,is_active) VALUES ('__tg_s1',:seen,13500,'priced','https://olx.ba/artikal/x','t',true)"""),
                      {"seen": self.now - timedelta(days=2)})
        message, items = sl.shortlist_message(self.engine, "en")
        self.assertIn("Your shortlist", message)
        self.assertIn("• Volkswagen Golf · 2016 · 12.000 KM · contacted", message)
        self.assertIn("↓ price changed since you saved it: was 13.500 KM", message)
        self.assertIn("⚠ Seen 4 weeks ago", message)  # the Octavia
        self.assertEqual({"__tg_s1", "__tg_s2"}, {i for i, _ in items})
        bosnian, _ = sl.shortlist_message(self.engine, "bs")
        self.assertIn("• Volkswagen Golf · 2016 · 12.000 KM · kontaktirano", bosnian)
        self.assertIn("• Škoda Octavia · 2018 · 17.000 KM · na listi", bosnian)
        self.assertIn("Tvoja lista", bosnian)

    def test_an_empty_shortlist(self):
        self.assertIn("shortlist is empty", sl.shortlist_message(self.engine, "en")[0])
        self.assertIn("Tvoja lista je prazna", sl.shortlist_message(self.engine, "bs")[0])

    def test_listing_never_writes(self):
        self.seed()
        sl.save_listing(self.engine, "__tg_s1")
        before = [tuple(r.values()) for r in self.candidates()]
        sl.shortlist_message(self.engine, "en")
        self.assertEqual(before, [tuple(r.values()) for r in self.candidates()])

    def role_engine(self):
        engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

        @event.listens_for(engine, "connect")
        def set_role(dbapi_connection, _record):
            # outside a transaction, so a later rollback cannot undo it
            dbapi_connection.autocommit = True
            cursor = dbapi_connection.cursor()
            cursor.execute(f"SET ROLE {self.ROLE}")
            cursor.close()
            dbapi_connection.autocommit = False

        return engine

    def test_without_the_sql_the_database_refuses_and_with_it_saving_works(self):
        """The proposed SQL, run against a role shaped like the bot's, row-level security on."""
        self.seed()
        self.cleanup_role()
        with self.engine.begin() as c:
            c.execute(text(f"CREATE ROLE {self.ROLE} NOLOGIN"))
            c.execute(text(f"GRANT USAGE ON SCHEMA public TO {self.ROLE}"))
            c.execute(text(f"GRANT SELECT ON car_listings, dealer_vehicle_labels, listing_snapshots, "
                           f"dealer_candidates TO {self.ROLE}"))
            c.execute(text("ALTER TABLE public.dealer_candidates ENABLE ROW LEVEL SECURITY"))
            for table in ("dealer_candidates", "car_listings", "dealer_vehicle_labels", "listing_snapshots"):
                # like the real role's private_app_read policies, in case row-level security is on
                c.execute(text(f"DROP POLICY IF EXISTS tg_read ON public.{table}"))
                c.execute(text(f"CREATE POLICY tg_read ON public.{table} FOR SELECT TO {self.ROLE} USING (true)"))
        bot = self.role_engine()
        with bot.connect() as c:
            self.assertEqual(self.ROLE, c.execute(text("SELECT current_user")).scalar())
        # before: the write is refused and reported as "not enabled", not as a crash
        with self.assertRaises(sl.NotEnabled):
            sl.save_listing(bot, "__tg_s1")
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO dealer_candidates(listing_id,status,note) VALUES ('__tg_s2','shortlisted','')"))
        with self.assertRaises(sl.NotEnabled):
            sl.remove_listing(bot, "__tg_s2")
        self.assertEqual(["__tg_s2"], [r["listing_id"] for r in self.candidates()])  # nothing changed
        # apply the proposed SQL, with the test role standing in for car_analyzer_app
        sql = (SQL_DIR / "shortlist_write_access.sql").read_text().replace("car_analyzer_app", self.ROLE)
        statements = [line for line in sql.splitlines() if not line.startswith("--")]
        with self.engine.begin() as c:
            c.execute(text("\n".join(statements)))
            c.execute(text("\n".join(statements)))  # idempotent
        bot.dispose()
        bot = self.role_engine()
        try:
            row, created = sl.save_listing(bot, "__tg_s1")
            self.assertTrue(created)
            self.assertFalse(sl.save_listing(bot, "__tg_s1")[1])
            self.assertEqual("removed", sl.remove_listing(bot, "__tg_s1")[1])
            self.assertEqual("removed", sl.remove_listing(bot, "__tg_s2")[1])
            # least privilege: the role cannot change a status or a note
            sl.save_listing(bot, "__tg_s1")
            with self.assertRaises(Exception) as refused:
                with bot.begin() as c:
                    c.execute(text("UPDATE dealer_candidates SET note='x' WHERE listing_id='__tg_s1'"))
            self.assertEqual("42501", refused.exception.orig.pgcode)
            # the rollback in the file restores the refusal
            rollback = [line[3:].strip() for line in sql.splitlines() if line.startswith("--   DROP") or line.startswith("--   REVOKE")]
            self.assertEqual(3, len(rollback))
            with self.engine.begin() as c:
                c.execute(text("\n".join(rollback)))
            bot.dispose()
            bot = self.role_engine()
            with self.assertRaises(sl.NotEnabled):
                sl.save_listing(bot, "__tg_s2")
        finally:
            bot.dispose()


if __name__ == "__main__":
    unittest.main()
