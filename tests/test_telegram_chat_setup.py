"""Which chats the bot answers in, and telling an allowed person when a group is not set up."""

import os
import unittest
from unittest.mock import Mock, patch

import telegram_bot
from telegram_bot import TelegramAPIError, TelegramConfig, TelegramConfigurationError, is_authorized, process_update
from telegram_digest import DigestConfig
from telegram_i18n import detect_language

BASE = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-5332163835",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(BASE)
NEW_GROUP = -1001234567890


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    for name in ("GEMINI_API_KEY", "TELEGRAM_DEFAULT_LANGUAGE"):
        os.environ.pop(name, None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def group_message(text_value, *, chat_id=NEW_GROUP, chat_type="supergroup", user=123, language_code=None, extra=None):
    sender = {"id": user}
    if language_code:
        sender["language_code"] = language_code
    message = {"chat": {"id": chat_id, "type": chat_type}, "from": sender}
    if text_value is not None:
        message["text"] = text_value
    message.update(extra or {})
    return {"update_id": 1, "message": message}


class ConfigTests(unittest.TestCase):
    def test_one_group_works_as_before(self):
        self.assertEqual(-5332163835, CONFIG.chat_id)
        self.assertEqual(frozenset(), CONFIG.extra_chat_ids)
        self.assertEqual(frozenset({-5332163835}), CONFIG.allowed_chats)

    def test_several_groups_can_be_listed_and_the_first_is_primary(self):
        config = TelegramConfig.from_env({**BASE, "TELEGRAM_CHAT_ID": f" -5332163835 , {NEW_GROUP},{NEW_GROUP}, -5332163835"})
        self.assertEqual(-5332163835, config.chat_id)
        self.assertEqual(frozenset({NEW_GROUP}), config.extra_chat_ids)
        self.assertEqual(frozenset({-5332163835, NEW_GROUP}), config.allowed_chats)

    def test_bad_group_lists_are_rejected(self):
        for bad in ("abc", "-1, x", ",", " , ", ""):
            with self.assertRaises(TelegramConfigurationError, msg=bad):
                TelegramConfig.from_env({**BASE, "TELEGRAM_CHAT_ID": bad})

    def test_the_digest_sends_to_the_first_group(self):
        env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": f"-5332163835, {NEW_GROUP}"}
        self.assertEqual(-5332163835, DigestConfig.from_env(env).chat_id)
        for bad in ("abc", ",", "-1, x"):
            with self.assertRaises(TelegramConfigurationError, msg=bad):
                DigestConfig.from_env({**env, "TELEGRAM_CHAT_ID": bad})


class AuthorizationTests(unittest.TestCase):
    def test_every_listed_group_is_allowed_for_allowed_people_only(self):
        config = TelegramConfig.from_env({**BASE, "TELEGRAM_CHAT_ID": f"-5332163835,{NEW_GROUP}"})
        for chat_id in (-5332163835, NEW_GROUP):
            self.assertTrue(is_authorized({"id": chat_id, "type": "supergroup"}, 123, config))
            self.assertFalse(is_authorized({"id": chat_id, "type": "supergroup"}, 999, config))
        self.assertFalse(is_authorized({"id": -42, "type": "supergroup"}, 123, config))
        self.assertTrue(is_authorized({"id": 123, "type": "private"}, 123, config))

    def test_the_bot_answers_in_a_second_listed_group(self):
        config = TelegramConfig.from_env({**BASE, "TELEGRAM_CHAT_ID": f"-5332163835,{NEW_GROUP}"})
        call = Mock(return_value={"message_id": 1})
        with patch("telegram_bot.render_command", return_value="status text"):
            result = process_update(group_message("/status"), Mock(), config, call=call, llm=None, phraser=None)
        self.assertTrue(result["handled"])
        self.assertEqual(NEW_GROUP, call.call_args.args[2]["chat_id"])


class NotSetUpHintTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS):
            store.clear()
        self.call = Mock(return_value={"message_id": 1})

    def run_update(self, update):
        with patch("telegram_bot.answer_intent") as answer, patch("telegram_bot.render_command") as render:
            result = process_update(update, Mock(), CONFIG, call=self.call, llm=None, phraser=None)
        answer.assert_not_called()
        render.assert_not_called()
        return result

    def test_an_allowed_person_addressing_the_bot_in_an_unknown_group_is_told_the_chat_id(self):
        for body in ("hej @mojagaraza_bot", "/status", "/help@mojagaraza_bot", "@mojagaraza_bot kako si"):
            self.call.reset_mock()
            result = self.run_update(group_message(body))
            self.assertEqual("unauthorized", result["reason"])
            payload = self.call.call_args.args[2]
            self.assertEqual(NEW_GROUP, payload["chat_id"])
            self.assertIn(str(NEW_GROUP), payload["text"])
            self.assertIn("TELEGRAM_CHAT_ID", payload["text"])
            self.assertEqual(1, self.call.call_count, body)

    def test_the_hint_follows_the_language_of_the_message(self):
        self.run_update(group_message("hej @mojagaraza_bot"))
        self.assertIn("Još nisam podešen", self.call.call_args.args[2]["text"])
        self.call.reset_mock()
        self.run_update(group_message("what's up @mojagaraza_bot"))
        self.assertIn("I'm not set up to answer in this chat yet", self.call.call_args.args[2]["text"])
        self.call.reset_mock()
        self.run_update(group_message("/status", language_code="bs"))
        self.assertIn("Još nisam podešen", self.call.call_args.args[2]["text"])

    def test_a_reply_to_the_bot_counts_as_addressing_it(self):
        reply = {"from": {"id": 9, "is_bot": True, "username": "mojagaraza_bot"}, "text": "x"}
        self.run_update(group_message("hvala", extra={"reply_to_message": reply}))
        self.assertIn(str(NEW_GROUP), self.call.call_args.args[2]["text"])

    def test_a_group_upgrade_is_announced_with_the_new_id(self):
        self.run_update(group_message(None, extra={"migrate_from_chat_id": -5332163835}))
        self.assertIn(str(NEW_GROUP), self.call.call_args.args[2]["text"])
        self.call.reset_mock()
        self.run_update(group_message(None, extra={"migrate_from_chat_id": -777}))  # some other group
        self.call.assert_not_called()

    def test_strangers_get_complete_silence(self):
        for body in ("hej @mojagaraza_bot", "/status", "/start"):
            self.run_update(group_message(body, user=999))
        self.run_update(group_message(None, user=999, extra={"migrate_from_chat_id": -5332163835}))
        self.call.assert_not_called()

    def test_ordinary_chatter_private_chats_and_buttons_stay_silent(self):
        self.run_update(group_message("hej svima"))                                   # not addressed
        self.run_update(group_message("hej @mojagaraza_bot", chat_type="private", chat_id=999))  # not a group
        self.run_update(group_message("hej @mojagaraza_bot", chat_type="channel"))
        self.run_update({"update_id": 2, "callback_query": {"id": "c", "from": {"id": 123}, "data": "status",
                         "message": {"chat": {"id": NEW_GROUP, "type": "supergroup"}, "message_id": 5}}})
        self.run_update({"update_id": 3, "my_chat_member": {"chat": {"id": NEW_GROUP, "type": "supergroup"}}})
        self.run_update(group_message("", user=123))
        self.call.assert_not_called()

    def test_the_hint_never_gives_access_or_runs_a_query(self):
        result = self.run_update(group_message("/status"))
        self.assertFalse(result["handled"])
        self.assertEqual("unauthorized", result["reason"])

    def test_a_failing_send_never_breaks_the_webhook(self):
        call = Mock(side_effect=TelegramAPIError("nope"))
        result = process_update(group_message("/status"), Mock(), CONFIG, call=call, llm=None, phraser=None)
        self.assertEqual("unauthorized", result["reason"])

    def test_the_log_line_has_ids_but_never_the_message_text(self):
        with self.assertLogs("telegram_bot", level="WARNING") as logs:
            self.run_update(group_message("hej @mojagaraza_bot tajna poruka 98765"))
        line = " ".join(logs.output)
        self.assertIn(str(NEW_GROUP), line)
        self.assertIn("supergroup", line)
        for secret in ("tajna", "98765", "test-only-token"):
            self.assertNotIn(secret, line)

    def test_the_hint_text_carries_nothing_but_the_chat_id(self):
        self.run_update(group_message("/status"))
        text = self.call.call_args.args[2]["text"]
        self.assertEqual(telegram_bot.t("en", "chat_not_set_up", chat_id=NEW_GROUP), text)  # nothing but the template
        for private in ("test-only-token", "test_only_webhook_secret_123", "5332163835"):
            self.assertNotIn(private, text)

    def test_hej_is_recognised_as_bosnian(self):
        self.assertEqual("bs", detect_language("hej @mojagaraza_bot"))


if __name__ == "__main__":
    unittest.main()
