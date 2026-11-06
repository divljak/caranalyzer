"""Group conversations: tag once, keep talking; Gemini knows where it is; ignored messages are explained."""

import json
import os
import unittest
from unittest.mock import Mock, patch

import telegram_bot
import telegram_llm
from telegram_bot import TelegramConfig, process_update
from telegram_intent import Filters, Intent

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-5332163835",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)
GROUP = -5332163835


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    for name in ("GEMINI_API_KEY", "TELEGRAM_DEFAULT_LANGUAGE", "TELEGRAM_GROUP_WINDOW_SECONDS"):
        os.environ.pop(name, None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def say(text_value, *, user=123, chat_id=GROUP, chat_type="supergroup", uid=1, extra=None):
    message = {"chat": {"id": chat_id, "type": chat_type}, "from": {"id": user}, "text": text_value}
    message.update(extra or {})
    return {"update_id": uid, "message": message}


class WindowTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS, telegram_bot._ACTIVE):
            store.clear()
        self.call = Mock(return_value={"message_id": 1})

    def run_update(self, update, config=CONFIG):
        with patch("telegram_bot.answer_intent", return_value=("answer", False)), \
                patch("telegram_bot.render_command", return_value="command answer"):
            return process_update(update, Mock(), config, call=self.call, llm=None, phraser=None)

    def test_tag_once_then_keep_talking(self):
        self.assertEqual("not_addressed", self.run_update(say("Golf do 15000 KM"))["reason"])  # before: ignored
        self.assertTrue(self.run_update(say("@mojagaraza_bot Golf do 15000 KM", uid=2))["handled"])
        for n, text_value in enumerate(("Golf 4", "a do 9000 KM?", "Octavia do 12000 KM"), 3):
            self.assertTrue(self.run_update(say(text_value, uid=n))["handled"], text_value)

    def test_the_window_belongs_to_one_person_in_one_group(self):
        self.run_update(say("@mojagaraza_bot hej", user=123))
        self.assertEqual("not_addressed", self.run_update(say("Golf 4", user=456, uid=2))["reason"])  # a friend
        self.assertTrue(self.run_update(say("Golf 4", user=123, uid=3))["handled"])
        config = TelegramConfig.from_env({**ENV, "TELEGRAM_CHAT_ID": f"{GROUP},-1009999"})
        other = self.run_update(say("Golf 4", chat_id=-1009999, uid=4), config)  # same person, other group
        self.assertEqual("not_addressed", other["reason"])

    def test_the_window_expires(self):
        self.run_update(say("@mojagaraza_bot hej"))
        with patch("telegram_bot.time.monotonic", return_value=telegram_bot.time.monotonic() + 299):
            self.assertTrue(self.run_update(say("Golf 4", uid=2))["handled"])
        with patch("telegram_bot.time.monotonic", return_value=telegram_bot.time.monotonic() + 700):
            self.assertEqual("not_addressed", self.run_update(say("Golf 4", uid=3))["reason"])

    def test_every_answer_extends_the_window(self):
        self.run_update(say("@mojagaraza_bot hej"))
        base = telegram_bot.time.monotonic()
        with patch("telegram_bot.time.monotonic", return_value=base + 250):
            self.run_update(say("a", uid=2))
        with patch("telegram_bot.time.monotonic", return_value=base + 500):  # 250 s after the last answer
            self.assertTrue(self.run_update(say("b", uid=3))["handled"])

    def test_commands_and_replies_also_start_a_window(self):
        self.run_update(say("/status"))
        self.assertTrue(self.run_update(say("Golf 4", uid=2))["handled"])
        telegram_bot._ACTIVE.clear()
        reply = {"from": {"id": 9, "is_bot": True}, "text": "earlier answer"}
        self.run_update(say("Octavia do 12000 KM", uid=3, extra={"reply_to_message": reply}))
        self.assertTrue(self.run_update(say("Golf 4", uid=4))["handled"])

    def test_a_stranger_never_starts_or_uses_a_window(self):
        result = self.run_update(say("@mojagaraza_bot hej", user=999))
        self.assertEqual("unauthorized", result["reason"])
        self.assertEqual({}, telegram_bot._ACTIVE)
        self.assertEqual("unauthorized", self.run_update(say("Golf 4", user=999, uid=2))["reason"])

    def test_it_can_be_shortened_or_switched_off(self):
        with patch.dict(os.environ, {"TELEGRAM_GROUP_WINDOW_SECONDS": "0"}):
            self.run_update(say("@mojagaraza_bot hej"))
            self.assertEqual("not_addressed", self.run_update(say("Golf 4", uid=2))["reason"])
        with patch.dict(os.environ, {"TELEGRAM_GROUP_WINDOW_SECONDS": "60"}):
            self.run_update(say("@mojagaraza_bot hej", uid=3))
            with patch("telegram_bot.time.monotonic", return_value=telegram_bot.time.monotonic() + 90):
                self.assertEqual("not_addressed", self.run_update(say("Golf 4", uid=4))["reason"])
        for bad in ("abc", "-5", "99999"):
            with patch.dict(os.environ, {"TELEGRAM_GROUP_WINDOW_SECONDS": bad}):
                self.assertIn(telegram_bot.group_window_seconds(), (300, 0, 3600), bad)

    def test_private_chats_are_unaffected_and_keep_no_window(self):
        private = {"update_id": 1, "message": {"chat": {"id": 123, "type": "private"}, "from": {"id": 123}, "text": "Golf"}}
        self.assertTrue(self.run_update(private)["handled"])
        self.assertEqual({}, telegram_bot._ACTIVE)

    def test_the_window_memory_is_bounded(self):
        for n in range(telegram_bot.SCOPE_MAX_ENTRIES + 25):
            telegram_bot._mark_conversation((n, n))
        self.assertLessEqual(len(telegram_bot._ACTIVE), telegram_bot.SCOPE_MAX_ENTRIES)
        telegram_bot._mark_conversation(None)
        self.assertNotIn(None, telegram_bot._ACTIVE)
        self.assertFalse(telegram_bot._in_conversation(None))


class GeminiKnowsWhereItIsTests(unittest.TestCase):
    CFG = telegram_llm.LLMConfig("key-0123456789", "gemini-3.5-flash-lite")

    def sent(self, post):
        return json.loads(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])

    def ok(self, payload):
        post = Mock()
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": json.dumps(payload)}]}}]}
        return post

    def test_both_calls_say_whether_it_is_a_group_or_a_private_chat(self):
        post = self.ok({"intent": "chitchat", "reply": "Hej!"})
        telegram_llm.interpret("hej", None, self.CFG, post=post, chat_kind="group")
        self.assertEqual("group", self.sent(post)["chat"])
        telegram_llm.interpret("hej", None, self.CFG, post=post, chat_kind="private")
        self.assertEqual("private", self.sent(post)["chat"])
        post = self.ok({"reply": "Evo.", "show_details": False})
        telegram_llm.phrase("x", "facts", self.CFG, post=post, chat_kind="group")
        self.assertEqual("group", self.sent(post)["chat"])
        telegram_llm.phrase("x", "facts", self.CFG, post=post)
        self.assertNotIn("chat", self.sent(post))
        telegram_llm.phrase("x", "facts", self.CFG, post=post, chat_kind="nonsense")
        self.assertNotIn("chat", self.sent(post))

    def test_the_prompts_forbid_claiming_it_sees_everything_in_a_group(self):
        post = self.ok({"intent": "chitchat", "reply": "Hej!"})
        telegram_llm.interpret("hej", None, self.CFG, post=post, chat_kind="group")
        prompt = post.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn('Never tell someone they can just write without tagging you', prompt)
        self.assertIn('"chat" field', prompt)
        post = self.ok({"reply": "Evo.", "show_details": False})
        telegram_llm.phrase("x", "facts", self.CFG, post=post, chat_kind="group")
        self.assertIn('"chat" field', post.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"])

    def test_the_bot_passes_the_chat_kind_through(self):
        seen = {}

        def fake_llm(message, previous, history, language="", chat_kind=""):
            seen["llm"] = chat_kind
            return Intent("chitchat", reply="Hej!")

        call = Mock(return_value={"message_id": 1})
        process_update(say("@mojagaraza_bot hej"), Mock(), CONFIG, call=call, llm=fake_llm, phraser=None)
        self.assertEqual("group", seen["llm"])
        private = {"update_id": 2, "message": {"chat": {"id": 123, "type": "private"}, "from": {"id": 123}, "text": "hej"}}
        process_update(private, Mock(), CONFIG, call=call, llm=fake_llm, phraser=None)
        self.assertEqual("private", seen["llm"])

    def test_the_help_text_explains_tagging_in_both_languages(self):
        self.assertIn("In a group, tag me or reply to one of my messages", telegram_bot.help_message("en"))
        self.assertIn("U grupi me označi ili odgovori na neku moju poruku", telegram_bot.help_message("bs"))


class IgnoredMessageDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS, telegram_bot._ACTIVE):
            store.clear()
        self.call = Mock(return_value={"message_id": 1})

    def run_update(self, update):
        return process_update(update, Mock(), CONFIG, call=self.call, llm=None, phraser=None)

    def test_an_ignored_addressed_message_leaves_a_reason_in_the_logs(self):
        with self.assertLogs("telegram_bot", level="WARNING") as logs:
            self.run_update(say("@mojagaraza_bot hej", user=999))
        line = " ".join(logs.output)
        self.assertIn("chat_type=supergroup chat_configured=True user_allowed=False", line)
        self.call.assert_not_called()  # still silent for strangers

    def test_the_log_never_contains_text_or_ids(self):
        with self.assertLogs("telegram_bot", level="WARNING") as logs:
            self.run_update(say("@mojagaraza_bot tajna poruka 98765", user=999))
        line = " ".join(logs.output)
        for secret in ("tajna", "98765", "999", "5332163835", "test-only-token"):
            self.assertNotIn(secret, line)

    def test_ordinary_chatter_is_not_logged(self):
        with self.assertNoLogs("telegram_bot", level="WARNING"):
            self.run_update(say("Golf 4", user=999))
            self.run_update(say("Golf 4"))

    def test_an_anonymous_admin_is_told_how_to_fix_it_in_a_configured_group_only(self):
        anonymous = telegram_bot.ANONYMOUS_ADMIN_ID
        with self.assertLogs("telegram_bot", level="WARNING"):
            result = self.run_update(say("@mojagaraza_bot hej", user=anonymous))
        self.assertEqual("unauthorized", result["reason"])
        self.assertIn("anonimni admin", self.call.call_args.args[2]["text"])
        self.call.reset_mock()
        self.run_update(say("@mojagaraza_bot hej", user=anonymous, chat_id=-1009999))  # not our group
        self.run_update(say("Golf 4", user=anonymous))                                  # not addressed
        self.run_update(say("/status", user=anonymous, chat_type="private", chat_id=anonymous))
        self.call.assert_not_called()

    def test_anonymous_admins_do_not_gain_any_access(self):
        with patch("telegram_bot.answer_intent") as answer, patch("telegram_bot.render_command") as render:
            self.run_update(say("/status", user=telegram_bot.ANONYMOUS_ADMIN_ID))
        answer.assert_not_called()
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
