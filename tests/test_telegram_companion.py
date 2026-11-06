"""Conversational replies: Gemini phrases database facts; every claim stays grounded."""

import json
import os
import unittest
from unittest.mock import Mock, patch

import telegram_bot
import telegram_llm
from telegram_bot import TelegramAPIError, TelegramConfig, process_update
from telegram_intent import Filters, Intent
from telegram_llm import LLMConfig, _numbers, intent_from_plan, interpret, phrase

KEY = "AIza-test-only-key-0123456789"
CFG = LLMConfig(KEY, "gemini-3.5-flash-lite")
ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)
FACTS = ("Observed listings · golf · up to 15.000 KM\n"
         "Showing 1–5 of 242 database matches, newest model year first:\n"
         "• Volkswagen Golf · 2015 · 13.900 KM · 182.000 km\n"
         "  Seen 3 days ago (03 Nov, 13:15) · https://olx.ba/artikal/80138618\n")


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    os.environ.pop("GEMINI_API_KEY", None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def ok(payload):
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"candidates": [
        {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(payload)}]}}]}
    return response


class ValidationTests(unittest.TestCase):
    def test_numbers_are_normalised(self):
        self.assertEqual({"15000"}, _numbers("15.000 KM"))
        self.assertEqual({"15000"}, _numbers("15,000 km"))
        self.assertEqual({"5", "13", "15"}, _numbers("05 Nov, 13:15"))
        self.assertTrue({"15000", "2015", "182000", "13900", "242", "5"} <= _numbers(FACTS))

    def test_phrased_reply_must_only_use_numbers_from_the_facts(self):
        good = {"reply": "Ima 242 Golfa do 15.000 KM, najnoviji je iz 2015.", "show_details": False}
        self.assertEqual((good["reply"], False), phrase("golf do 15000", FACTS, CFG, post=Mock(return_value=ok(good))))
        for bad_reply in ("Ima oko 300 Golfa.", "Golf 2015 je pouzdan, 14.500 KM.", "242 Golfa, pogledaj olx.ba/artikal",
                          "Vidi https://olx.ba/artikal/1", "Pitaj @neko", "x" * 700, "", "242 `code`"):
            plan = {"reply": bad_reply, "show_details": True}
            self.assertIsNone(phrase("golf do 15000", FACTS, CFG, post=Mock(return_value=ok(plan))), bad_reply)

    def test_numbers_from_the_user_message_and_history_are_allowed(self):
        plan = {"reply": "Za 12000 KM nema ništa novo.", "show_details": False}
        self.assertIsNotNone(phrase("a za 12000?", FACTS, CFG, post=Mock(return_value=ok(plan))))
        plan = {"reply": "Tražili smo do 9000 KM.", "show_details": False}
        history = [{"role": "user", "text": "golf do 9000"}]
        self.assertIsNotNone(phrase("hm", FACTS, CFG, post=Mock(return_value=ok(plan)), history=history))

    def test_show_details_is_true_only_for_a_real_boolean(self):
        for value, expected in ((True, True), (False, False), ("true", False), (1, False), (None, False)):
            plan = {"reply": "Evo.", "show_details": value}
            self.assertEqual(expected, phrase("x", FACTS, CFG, post=Mock(return_value=ok(plan)))[1], value)

    def test_phrase_sends_only_facts_message_and_recent_turns(self):
        post = Mock(return_value=ok({"reply": "Evo.", "show_details": False}))
        history = [{"role": "user", "text": "hi"}, {"role": "bot", "text": "Zdravo!"}]
        phrase("golf do 15000", FACTS, CFG, post=post, history=history)
        body = post.call_args.kwargs["json"]
        sent = json.loads(body["contents"][0]["parts"][0]["text"])
        self.assertEqual({"message", "facts", "recent_turns"}, set(sent))
        self.assertEqual([{"role": "user", "text": "hi"}, {"role": "assistant", "text": "Zdravo!"}],
                         sent["recent_turns"])
        self.assertEqual(KEY, post.call_args.kwargs["headers"]["x-goog-api-key"])
        self.assertNotIn(KEY, json.dumps(body))
        self.assertIn("ONLY the database", body["systemInstruction"]["parts"][0]["text"])

    def test_suggestion_mode_widens_the_prompt_but_keeps_the_grounding_rules(self):
        post = Mock(return_value=ok({"reply": "Evo.", "show_details": True}))
        phrase("šta da kupim", FACTS, CFG, post=post, mode="suggest")
        prompt = post.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("data-based shortlist", prompt)
        self.assertIn("most listed is not best selling", prompt)
        self.assertIn("never recommend a specific car from your own knowledge", prompt)
        post = Mock(return_value=ok({"reply": "Evo.", "show_details": True}))
        phrase("golf", FACTS, CFG, post=post)
        self.assertNotIn("data-based shortlist", post.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"])
        invented = {"reply": "Najpametnije je 7 Golfa za 4000 KM.", "show_details": True}
        self.assertIsNone(phrase("šta da kupim", FACTS, CFG, post=Mock(return_value=ok(invented)), mode="suggest"))

    def test_oversized_facts_are_truncated_and_failures_return_none(self):
        post = Mock(return_value=ok({"reply": "Evo.", "show_details": False}))
        phrase("x", "a" * 10000, CFG, post=post)
        sent = json.loads(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertEqual(telegram_llm.MAX_FACTS_CHARS, len(sent["facts"]))
        bad = Mock(status_code=429)
        bad.json.return_value = {}
        self.assertIsNone(phrase("x", FACTS, CFG, post=Mock(return_value=bad)))
        self.assertIsNone(phrase("x", "", CFG, post=Mock()))
        self.assertIsNone(phrase("x", FACTS, None, post=Mock()))
        with self.assertLogs("telegram_llm", level="WARNING") as logs:
            self.assertIsNone(phrase("secret text", FACTS, CFG, post=Mock(side_effect=ValueError(KEY))))
        self.assertNotIn(KEY, " ".join(logs.output))
        self.assertNotIn("secret text", " ".join(logs.output))

    def test_chat_replies_from_the_planner_must_carry_no_new_facts(self):
        good = intent_from_plan({"intent": "chitchat", "reply": "Zdravo! Šta tražiš?"}, None, "bok")
        self.assertEqual(("chitchat", "Zdravo! Šta tražiš?"), (good.kind, good.reply))
        self.assertEqual("", intent_from_plan({"intent": "chitchat", "reply": "Golf košta 9000 KM"}, None, "bok").reply)
        self.assertEqual("Golf do 9000 je ok pitanje", intent_from_plan(
            {"intent": "unsupported", "reply": "Golf do 9000 je ok pitanje"}, None, "golf do 9000 ?").reply)
        self.assertEqual("", intent_from_plan({"intent": "clarify", "reply": "Pogledaj https://x.example"}, None, "x").reply)
        clar = intent_from_plan({"intent": "clarify", "reply": "Koji ti je budžet?"}, None, "treba mi auto")
        self.assertEqual("Koji ti je budžet?", clar.reply)

    def test_history_is_sent_to_the_planner(self):
        post = Mock(return_value=ok({"intent": "chitchat", "reply": "Hej!"}))
        history = [{"role": "user", "text": "golf do 15000"}, {"role": "bot", "text": "Našao sam 242."}]
        interpret("hvala", None, CFG, post=post, history=history)
        sent = json.loads(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertEqual(2, len(sent["recent_turns"]))


def dm(text, *, user=123, chat_id=123, chat_type="private", uid=1):
    return {"update_id": uid, "message": {"chat": {"id": chat_id, "type": chat_type},
                                           "from": {"id": user}, "text": text}}


class CompanionFlowTests(unittest.TestCase):
    def setUp(self):
        telegram_bot._SCOPES.clear()
        telegram_bot._HISTORY.clear()
        self.call = Mock()
        self.llm = Mock(return_value=None)
        self.phraser = Mock(return_value=None)

    def run_update(self, update, *, llm=None, phraser=None, facts=FACTS, markup=None, db=True):
        """db=True answers searches with canned facts; db=False uses the real chat answers."""
        patches = [patch("telegram_bot.render_command", return_value="command answer")]
        if db:
            patches.append(patch("telegram_bot.answer_intent", return_value=(facts, markup)))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return process_update(update, Mock(), CONFIG, call=self.call,
                              llm=llm or self.llm, phraser=phraser or self.phraser)

    def sent_text(self):
        return [c.args[2]["text"] for c in self.call.call_args_list if c.args[1] == "sendMessage"][-1]

    def test_database_answers_become_chat_replies_without_the_listing_dump(self):
        self.phraser.return_value = ("Našao sam 242 Golfa do 15.000 KM. Reci ako želiš da ti ih pokažem.", False)
        self.run_update(dm("Golf do 15000 KM"))
        self.assertEqual("Našao sam 242 Golfa do 15.000 KM. Reci ako želiš da ti ih pokažem.", self.sent_text())
        message, facts, history = self.phraser.call_args.args
        self.assertEqual("Golf do 15000 KM", message)
        self.assertIn("242 database matches", facts)
        self.llm.assert_not_called()  # clear question: the free parser reads it, Gemini only phrases

    def test_listings_are_attached_when_the_model_says_they_help(self):
        self.phraser.return_value = ("Evo ih.", True)
        self.run_update(dm("show me golfs under 15000 KM"))
        self.assertEqual("Evo ih.\n\n" + FACTS, self.sent_text())

    def test_without_a_valid_phrasing_the_plain_database_answer_is_sent(self):
        self.run_update(dm("Golf do 15000 KM"))
        self.assertEqual(FACTS, self.sent_text())

    def test_suggestion_requests_always_attach_the_listings(self):
        self.phraser.return_value = ("Evo šta podaci pokazuju.", False)
        self.run_update(dm("imam 50k budzet, šta da kupim"), facts="Data-based shortlist · up to 50.000 KM\n• x")
        self.assertEqual("Evo šta podaci pokazuju.\n\nData-based shortlist · up to 50.000 KM\n• x", self.sent_text())
        self.assertEqual("suggest", self.phraser.call_args.kwargs["mode"])
        self.llm.assert_not_called()  # the free parser already understood it

    def test_buttons_and_commands_stay_deterministic(self):
        self.phraser.return_value = ("chatty", False)
        update = {"update_id": 5, "callback_query": {"id": "c", "from": {"id": 123}, "data": "f:15000:0:0:5:golf",
                                                      "message": {"chat": {"id": 123, "type": "private"}}}}
        self.run_update(update)
        self.run_update(dm("/status", uid=6))
        self.run_update(dm("/budget 25000 Golf", uid=7))
        self.phraser.assert_not_called()
        self.llm.assert_not_called()

    def test_small_talk_gets_a_friendly_reply_and_a_typing_indicator(self):
        plan = Intent("chitchat", reply="Zdravo! Šta tražiš danas?")
        result = self.run_update(dm("bok bok"), llm=Mock(return_value=plan), db=False)
        self.assertEqual("Zdravo! Šta tražiš danas?", self.sent_text())
        self.assertEqual("chitchat", result["command"])
        self.assertEqual("sendChatAction", self.call.call_args_list[0].args[1])
        self.assertEqual("typing", self.call.call_args_list[0].args[2]["action"])
        self.phraser.assert_not_called()

    def test_typing_indicator_failures_never_block_the_answer(self):
        def flaky(config, method, payload):
            if method == "sendChatAction":
                raise TelegramAPIError("nope")
        call = Mock(side_effect=flaky)
        with patch("telegram_bot.answer_intent", return_value=(FACTS, None)):
            result = process_update(dm("Golf do 15000 KM"), Mock(), CONFIG, call=call,
                                    llm=self.llm, phraser=self.phraser)
        self.assertTrue(result["handled"])

    def test_thanks_gets_a_reply_with_gemini_and_silence_without(self):
        plan = Intent("chitchat", reply="Nema na čemu!")
        result = self.run_update(dm("thanks"), llm=Mock(return_value=plan), db=False)
        self.assertTrue(result["handled"])
        self.assertEqual("Nema na čemu!", self.sent_text())
        telegram_bot._HISTORY.clear()
        self.call.reset_mock()
        result = process_update(dm("thanks", uid=2), Mock(), CONFIG, call=self.call, llm=None, phraser=None)
        self.assertEqual("acknowledgement", result["reason"])
        self.call.assert_not_called()

    def test_empty_model_reply_falls_back_to_the_fixed_text(self):
        self.run_update(dm("bok"), llm=Mock(return_value=Intent("chitchat", reply="")), db=False)
        self.assertIn("used cars", self.sent_text())
        self.run_update(dm("treba mi auto", uid=2), llm=Mock(return_value=Intent("clarify", reply="")), db=False)
        self.assertIn("can't pick a car", self.sent_text())

    def test_conversation_history_is_remembered_in_private_chats_only_and_per_user(self):
        self.phraser.return_value = ("Našao sam 242.", False)
        self.run_update(dm("Golf do 15000 KM", uid=1))
        history = self.llm_history_for("hvala ti")
        self.assertEqual([{"role": "user", "text": "Golf do 15000 KM"}, {"role": "bot", "text": "Našao sam 242."}], history)
        self.assertEqual([], self.llm_history_for("hvala ti", user=456, chat_id=456, uid=3))  # another user
        group = dm("@mojagaraza_bot Golf do 15000 KM", chat_id=CONFIG.chat_id, chat_type="supergroup", uid=4)
        self.run_update(group)
        self.assertEqual([], self.llm_history_for("@mojagaraza_bot hvala", chat_id=CONFIG.chat_id,
                                                  chat_type="supergroup", uid=5))  # groups keep none

    def llm_history_for(self, text, **kwargs):
        seen = []
        llm = Mock(side_effect=lambda message, previous, history: seen.append(history) or None)
        self.run_update(dm(text, **kwargs), llm=llm, db=False)
        return seen[0]

    def test_history_expires_and_is_bounded(self):
        for n in range(telegram_bot.HISTORY_MAX_TURNS * 3):
            telegram_bot._remember_turn((1, 1), f"q{n}", f"a{n}")
        self.assertEqual(telegram_bot.HISTORY_MAX_TURNS, len(telegram_bot._recall_history((1, 1))))
        with patch("telegram_bot.time.monotonic", return_value=10 ** 9):
            self.assertEqual([], telegram_bot._recall_history((1, 1)))
        for n in range(telegram_bot.SCOPE_MAX_ENTRIES + 20):
            telegram_bot._remember_turn((n, n), "q", "a")
        self.assertLessEqual(len(telegram_bot._HISTORY), telegram_bot.SCOPE_MAX_ENTRIES)
        self.assertEqual([], telegram_bot._recall_history(None))

    def test_unauthorized_senders_never_reach_gemini(self):
        for update in (dm("hello", user=999, chat_id=999), dm("hello", user=123, chat_id=999)):
            result = self.run_update(update, llm=Mock(), phraser=Mock())
            self.assertEqual("unauthorized", result["reason"])
        self.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
