"""Interaction log (best effort, privacy-preserving) and the durable digest ledger."""

import os
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

import telegram_bot
import telegram_log
from telegram_bot import TelegramConfig, process_update
from telegram_digest import DatabaseLedger, MemoryLedger
from telegram_intent import Filters, Intent

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-5332163835",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
    "TELEGRAM_BOT_USERNAME": "mojagaraza_bot",
}
CONFIG = TelegramConfig.from_env(ENV)
ROOT = Path(__file__).resolve().parents[1]


class PgError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.pgcode = code


def failing_engine(code):
    """An engine whose every statement fails with a PostgreSQL error code."""
    engine = Mock()
    connection = Mock()
    connection.execute.side_effect = ProgrammingError("stmt", {}, PgError(code))
    engine.begin.return_value.__enter__ = Mock(return_value=connection)
    engine.begin.return_value.__exit__ = Mock(return_value=False)
    return engine


class RedactionTests(unittest.TestCase):
    def test_contact_details_and_handles_are_removed_but_car_numbers_stay(self):
        value = ("@mojagaraza_bot golf do 15.000 KM, 150000 km, zovi +387 61 123 456 ili 061/123-456, "
                 "mail ogi.test@example.com, pitaj @marko, od 2016")
        cleaned = telegram_log.redact(value, "mojagaraza_bot")
        self.assertEqual("@mojagaraza_bot golf do 15.000 KM, 150000 km, zovi [phone] ili [phone], "
                         "mail [email], pitaj @user, od 2016", cleaned)
        self.assertLessEqual(len(telegram_log.redact("x" * 900)), telegram_log.MAX_TEXT)

    def test_user_ids_are_hashed_with_a_salt(self):
        first = telegram_log.user_hash(123, "salt-a")
        self.assertRegex(first, r"^[0-9a-f]{24}$")
        self.assertEqual(first, telegram_log.user_hash(123, "salt-a"))
        self.assertNotEqual(first, telegram_log.user_hash(123, "salt-b"))
        self.assertNotEqual(first, telegram_log.user_hash(456, "salt-a"))
        self.assertNotIn("123", first)
        self.assertIsNone(telegram_log.user_hash(123, ""))
        self.assertEqual("custom", telegram_log.salt(CONFIG, {"TELEGRAM_LOG_SALT": "custom"}))
        self.assertEqual(CONFIG.webhook_secret, telegram_log.salt(CONFIG, {}))

    def test_a_record_carries_no_names_or_chat_ids(self):
        record = telegram_log.build_record(
            chat_kind="group", user_id=123, secret="s", message_text="golf do 9000 KM",
            intent=Intent("find", Filters("golf", 9000, family=True)), path="parser", result_count=12,
            shown_count=5, phrasing="fallback", phrasing_reason="rejected: number not in the facts", latency_ms=812.4)
        self.assertEqual('{"search": "golf", "budget": 9000, "family": true}', record["filters"])
        self.assertEqual(("find", 12, 5, 812), (record["intent"], record["result_count"], record["shown_count"],
                                                record["latency_ms"]))
        self.assertNotIn(123, record.values())
        self.assertNotIn("-5332163835", str(record))


class NeverBreaksTests(unittest.TestCase):
    def setUp(self):
        telegram_log._disabled_until[0] = 0.0
        self.addCleanup(lambda: telegram_log._disabled_until.__setitem__(0, 0.0))

    def test_a_missing_table_switches_logging_off_quietly(self):
        engine = failing_engine("42P01")
        with patch.object(telegram_log, "Engine", Mock):  # treat the fake as a real engine
            with self.assertNoLogs("telegram_log", level="WARNING"):
                telegram_log.record(engine, {"intent": "find"})
            self.assertFalse(telegram_log.enabled())
            engine.begin.reset_mock()
            telegram_log.record(engine, {"intent": "find"})
            engine.begin.assert_not_called()

    def test_other_errors_log_only_a_class_name(self):
        engine = failing_engine("57014")  # statement timeout
        with patch.object(telegram_log, "Engine", Mock):
            with self.assertLogs("telegram_log", level="WARNING") as logs:
                telegram_log.record(engine, {"message_text": "secret golf text"})
        self.assertIn("ProgrammingError", logs.output[0])
        self.assertNotIn("secret golf text", logs.output[0])
        self.assertTrue(telegram_log.enabled())

    def test_a_slow_database_never_holds_the_caller_long(self):
        release = threading.Event()
        engine = Mock()
        engine.begin.side_effect = lambda: release.wait(5) and None
        with patch.object(telegram_log, "Engine", Mock):
            started = time.monotonic()
            worker = telegram_log.record(engine, {}, wait=0.2)
            self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(worker.daemon)
        release.set()
        worker.join(2)  # let it finish: its late "not logged" warning must not land in the next test's log capture

    def test_the_kill_switch(self):
        self.assertFalse(telegram_log.enabled({"TELEGRAM_INTERACTION_LOG": "off"}))

    def test_logging_happens_after_the_reply_and_records_the_path(self):
        order = []
        call = Mock(side_effect=lambda _c, method, _p: order.append(method) or {"message_id": 1})
        with patch("telegram_log.record", side_effect=lambda engine, entry, **kw: order.append(("log", entry))), \
                patch("telegram_bot.find_page", return_value=(telegram_bot.Html("<b>x</b>"), 12,
                                                              {"items": (("1", 2015, 9000),) * 5})):
            process_update({"update_id": 1, "message": {"chat": {"id": 123, "type": "private"},
                                                         "from": {"id": 123}, "text": "golf do 9000 KM"}},
                           Mock(), CONFIG, call=call, llm=None, phraser=None)
        self.assertEqual("sendMessage", order[0])
        kind, entry = order[-1]
        self.assertEqual("log", kind)
        self.assertEqual(("private", "find", "parser", 12, 5, "off"),
                         (entry["chat_type"], entry["intent"], entry["path"], entry["result_count"],
                          entry["shown_count"], entry["phrasing"]))
        self.assertEqual("golf do 9000 KM", entry["message_text"])

    def test_gemini_fallback_reason_is_recorded(self):
        entries = []

        def phraser(*_a, **_k):
            import telegram_llm
            telegram_llm.PHRASE_STATUS.set("rejected: number not in the facts")
            return None

        with patch("telegram_log.record", side_effect=lambda engine, entry, **kw: entries.append(entry)), \
                patch("telegram_bot.find_page", return_value=("x", 0, {})):
            process_update({"update_id": 1, "message": {"chat": {"id": 123, "type": "private"},
                                                         "from": {"id": 123}, "text": "golf do 9000 KM"}},
                           Mock(), CONFIG, call=Mock(return_value={"message_id": 1}), llm=None, phraser=phraser)
        self.assertEqual(("fallback", "rejected: number not in the facts"),
                         (entries[0]["phrasing"], entries[0]["phrasing_reason"]))

    def test_a_logging_crash_never_reaches_the_user(self):
        call = Mock(return_value={"message_id": 1})
        with patch("telegram_log.build_record", side_effect=RuntimeError("boom")), \
                patch("telegram_bot.render_command", return_value="ok"):
            result = process_update({"update_id": 1, "message": {"chat": {"id": 123, "type": "private"},
                                                                  "from": {"id": 123}, "text": "/status"}},
                                    Mock(), CONFIG, call=call, llm=None, phraser=None)
        self.assertTrue(result["handled"])


class LedgerFallbackTests(unittest.TestCase):
    def test_without_the_table_the_old_ledger_is_used(self):
        fallback = MemoryLedger()
        ledger = DatabaseLedger(failing_engine("42P01"), fallback)
        with self.assertLogs("telegram_digest", level="WARNING"):
            self.assertTrue(ledger.claim("2026-11-15"))
        self.assertFalse(ledger.claim("2026-11-15"))
        ledger.release("2026-11-15")
        self.assertTrue(fallback.claim("2026-11-15"))
        self.assertFalse(ledger.durable)

    def test_other_database_errors_fail_closed(self):
        ledger = DatabaseLedger(failing_engine("08006"), MemoryLedger())
        with self.assertRaises(ProgrammingError):
            ledger.claim("2026-11-15")


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class DurableTablesDbTests(unittest.TestCase):
    """Apply the two proposed migrations to the throwaway database and use them like production would."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])
        with cls.engine.begin() as c:
            if c.execute(text("SELECT 1 FROM pg_roles WHERE rolname='car_analyzer_app'")).scalar() is None:
                c.execute(text("CREATE ROLE car_analyzer_app"))
            for name in ("telegram_digest_sends.sql", "bot_interactions.sql"):
                sql = (ROOT / "docs" / "proposed-migrations" / name).read_text()
                if c.execute(text("SHOW server_version_num")).scalar() < "150000":
                    sql = sql.replace(" WITH (security_invoker = true)", "")
                c.connection.cursor().execute(sql)  # no parameters: '%' in LIKE stays literal

    @classmethod
    def tearDownClass(cls):
        with cls.engine.begin() as c:
            c.execute(text("DROP VIEW IF EXISTS bot_interactions_weekly"))
            c.execute(text("DROP TABLE IF EXISTS bot_interactions"))
            c.execute(text("DROP TABLE IF EXISTS telegram_digest_sends"))
        cls.engine.dispose()

    def test_one_digest_per_day_across_instances(self):
        first, second = DatabaseLedger(self.engine, MemoryLedger(), 7), DatabaseLedger(self.engine, MemoryLedger(), 7)
        self.assertTrue(first.claim("2026-11-15"))
        self.assertFalse(second.claim("2026-11-15"))  # another instance, same day
        first.release("2026-11-15")
        self.assertTrue(second.claim("2026-11-15"))  # a failed send can be retried
        second.mark_sent("2026-11-15", 42)
        second.release("2026-11-15")  # a sent digest is never released
        self.assertFalse(first.claim("2026-11-15"))
        self.assertTrue(first.durable)
        with self.engine.connect() as c:
            row = c.execute(text("SELECT chat_id,message_id,sent_at IS NOT NULL FROM telegram_digest_sends")).one()
        self.assertEqual((7, 42, True), tuple(row))

    def test_interactions_are_inserted_and_summarised(self):
        telegram_log._disabled_until[0] = 0.0
        entry = telegram_log.build_record(chat_kind="group", user_id=123, secret="s", message_text="golf do 9000 KM",
                                          intent=Intent("find", Filters("golf", 9000)), path="parser",
                                          result_count=0, shown_count=0, phrasing="fallback",
                                          phrasing_reason="rejected: number not in the facts", latency_ms=420)
        telegram_log.record(self.engine, entry, wait=5)
        with self.engine.connect() as c:
            row = c.execute(text("SELECT chat_type,intent,filters->>'search',result_count FROM bot_interactions")).one()
            weekly = c.execute(text("SELECT interactions,phrasing_fallbacks,empty_results FROM bot_interactions_weekly")).one()
        self.assertEqual(("group", "find", "golf", 0), tuple(row))
        self.assertEqual((1, 1, 1), tuple(weekly))
        summary = (ROOT / "scripts" / "bot_interactions_weekly.sql").read_text()
        with self.engine.connect() as c:
            cursor = c.connection.cursor()
            for statement in filter(str.strip, summary.split(";")):
                if "SELECT" in statement:
                    cursor.execute(statement)
                    cursor.fetchall()


if __name__ == "__main__":
    unittest.main()
