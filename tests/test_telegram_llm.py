"""Gemini fallback, private-chat access and best-effort memory for the Telegram bot."""

import json
import os
import unittest
from unittest.mock import Mock, patch

import requests

import telegram_bot
import telegram_llm
from telegram_bot import TelegramConfig, process_update
from telegram_intent import Filters, Intent
from telegram_llm import LLMConfig, call_plan, check_message, intent_from_plan, interpret

KEY = "AIza-test-only-key-0123456789"
CFG = LLMConfig(KEY, "gemini-3.5-flash-lite")
ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("TELEGRAM_LLM_MODEL", None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def reply(code=200, body=None, text=None):
    response = Mock()
    response.status_code = code
    payload = body if body is not None else {
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]}
    response.json.return_value = payload
    return response


def plan_reply(plan):
    return reply(text=json.dumps(plan))


class ConfigAndValidationTests(unittest.TestCase):
    def test_config_needs_a_key_and_a_sane_model_name(self):
        self.assertIsNone(LLMConfig.from_env({}))
        self.assertIsNone(LLMConfig.from_env({"GEMINI_API_KEY": "  "}))
        self.assertEqual("gemini-3.5-flash-lite", LLMConfig.from_env({"GEMINI_API_KEY": "k"}).model)
        self.assertEqual("gemini-x.1", LLMConfig.from_env(
            {"GEMINI_API_KEY": "k", "TELEGRAM_LLM_MODEL": "gemini-x.1"}).model)
        self.assertIsNone(LLMConfig.from_env(
            {"GEMINI_API_KEY": "k", "TELEGRAM_LLM_MODEL": "../evil?x=1"}))

    def test_plans_are_validated_not_trusted(self):
        plan = {"intent": "find", "search": "golf'; DROP TABLE x; --", "budget_km": 15000,
                "year_min": 1500, "mileage_max": True}
        intent = intent_from_plan(plan)
        self.assertEqual("find", intent.kind)
        self.assertEqual(Filters("golf DROP TABLE x --", 15000, None, None), intent.filters)
        self.assertIsNone(intent_from_plan({"intent": "run_sql"}))
        self.assertIsNone(intent_from_plan("nope"))
        self.assertIsNone(intent_from_plan({"intent": ["find"]}))
        self.assertEqual("clarify", intent_from_plan({"intent": "find"}).kind)
        self.assertEqual("clarify", intent_from_plan(
            {"intent": "compare", "compare_a": "golf", "compare_b": "Golf"}).kind)
        self.assertEqual(("golf", "octavia"), intent_from_plan(
            {"intent": "compare", "compare_a": "golf", "compare_b": "octavia"}).labels)
        self.assertEqual(90, intent_from_plan({"intent": "trends", "days": 90}).days)
        self.assertEqual(7, intent_from_plan({"intent": "trends", "days": 9000}).days)

    def test_suggest_plans_need_a_budget_within_limits(self):
        ok = intent_from_plan({"intent": "suggest", "budget_km": 50000, "year_min": 2015, "mileage_max": 200000})
        self.assertEqual(("suggest", Filters("", 50000, 2015, 200000)), (ok.kind, ok.filters))
        for plan in ({"intent": "suggest"}, {"intent": "suggest", "budget_km": 5},
                     {"intent": "suggest", "budget_km": True}):
            self.assertEqual("clarify", intent_from_plan(plan).kind, plan)

    def test_clarifying_question_cannot_smuggle_facts_or_links(self):
        ok = intent_from_plan({"intent": "clarify", "clarifying_question": "What budget do you have in mind?"})
        self.assertEqual("What budget do you have in mind?", ok.question)
        for bad in ("Golfs cost 9000 KM", "See https://evil.example", "ping @someone", "x" * 300):
            self.assertEqual("", intent_from_plan({"intent": "clarify", "clarifying_question": bad}).question)

    def test_refinement_merges_only_with_a_previous_find(self):
        previous = Intent("find", Filters("golf", 15000))
        merged = intent_from_plan({"intent": "find", "year_min": 2018, "refines_previous": True}, previous)
        self.assertEqual(Filters("golf", 15000, 2018), merged.filters)
        alone = intent_from_plan({"intent": "find", "year_min": 2018, "refines_previous": True}, None)
        self.assertEqual(Filters("", None, 2018), alone.filters)


class CallTests(unittest.TestCase):
    def test_key_goes_in_a_header_and_never_in_the_url_or_body(self):
        post = Mock(return_value=plan_reply({"intent": "status"}))
        plan, status = call_plan("is it working?", None, CFG, post=post)
        self.assertEqual({"intent": "status"}, plan)
        url = post.call_args.args[0]
        self.assertTrue(url.endswith("/models/gemini-3.5-flash-lite:generateContent"))
        self.assertNotIn(KEY, url)
        self.assertEqual(KEY, post.call_args.kwargs["headers"]["x-goog-api-key"])
        self.assertNotIn(KEY, json.dumps(post.call_args.kwargs["json"]))
        self.assertNotIn("params", post.call_args.kwargs)

    def test_prompt_asks_for_a_budget_first(self):
        post = Mock(return_value=plan_reply({"intent": "clarify"}))
        call_plan("something sensible for my family", None, CFG, post=post)
        prompt = post.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("Ask for the maximum budget in KM first", prompt)

    def test_only_the_message_and_prior_scope_are_sent(self):
        post = Mock(return_value=plan_reply({"intent": "find", "search": "golf"}))
        call_plan("ima li nesto jeftino", Intent("find", Filters("golf", 9000)), CFG, post=post)
        sent = json.loads(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertEqual({"message", "previous_scope"}, set(sent))
        self.assertEqual({"intent": "find", "search": "golf", "budget_km": 9000, "year_min": None,
                          "mileage_max": None}, sent["previous_scope"])

    def test_rejected_request_shape_retries_with_simpler_ones(self):
        post = Mock(side_effect=[reply(400, {}), reply(400, {}), plan_reply({"intent": "latest"})])
        plan, status = call_plan("what's up", None, CFG, post=post)
        self.assertEqual({"intent": "latest"}, plan)
        self.assertEqual(3, post.call_count)
        configs = [c.kwargs["json"]["generationConfig"] for c in post.call_args_list]
        self.assertIn("thinkingConfig", configs[0])
        self.assertNotIn("thinkingConfig", configs[1])
        self.assertNotIn("responseJsonSchema", configs[2])
        self.assertEqual("application/json", configs[2]["responseMimeType"])

    def test_invalid_key_reported_as_400_is_not_retried(self):
        body = {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                          "message": "API key not valid. Please pass a valid API key.",
                          "details": [{"reason": "API_KEY_INVALID"}]}}
        post = Mock(return_value=reply(400, body))
        plan, status = call_plan("x", None, CFG, post=post)
        self.assertIsNone(plan)
        self.assertIn("key rejected", status)
        self.assertEqual(1, post.call_count)

    def test_failures_return_no_plan_and_a_safe_status(self):
        cases = [
            (reply(429, {}), "429"), (reply(401, {}), "rejected"), (reply(403, {}), "rejected"),
            (reply(404, {}), "not found"), (reply(500, {}), "500"),
            (reply(200, {"candidates": []}), "empty"),
            (reply(200, {"candidates": [{"finishReason": "SAFETY"}]}), "empty"),
            (reply(200, {"candidates": [{"finishReason": "MAX_TOKENS",
                                         "content": {"parts": [{"text": "{"}]}}]}), "empty"),
            (reply(text="not json"), "valid JSON"),
        ]
        for response, expected in cases:
            plan, status = call_plan("x", None, CFG, post=Mock(return_value=response))
            self.assertIsNone(plan)
            self.assertIn(expected, status)
            self.assertNotIn(KEY, status)
        plan, status = call_plan("x", None, CFG, post=Mock(side_effect=requests.Timeout(KEY)))
        self.assertIsNone(plan)
        self.assertNotIn(KEY, status)

    def test_fenced_json_is_accepted(self):
        post = Mock(return_value=reply(text='```json\n{"intent": "status"}\n```'))
        self.assertEqual({"intent": "status"}, call_plan("x", None, CFG, post=post)[0])

    def test_interpret_never_raises_and_logs_no_content(self):
        for post in (Mock(side_effect=ValueError("boom")), Mock(side_effect=requests.ConnectionError(KEY))):
            with self.assertLogs("telegram_llm", level="WARNING") as logs:
                self.assertIsNone(interpret("secret question text", None, CFG, post=post))
            joined = " ".join(logs.output)
            self.assertNotIn(KEY, joined)
            self.assertNotIn("secret question text", joined)
        self.assertIsNone(interpret("hi", None, None))
        self.assertIsNone(interpret("   ", None, CFG, post=Mock()))

    def test_check_message_reports_status_without_secrets(self):
        self.assertIn("not configured", check_message(None))
        ok = check_message(CFG, post=Mock(return_value=plan_reply({"intent": "find", "search": "golf", "budget_km": 15000})))
        self.assertIn("ok", ok)
        self.assertIn("understood as: find", ok)
        failed = check_message(CFG, post=Mock(return_value=reply(429, {})))
        self.assertIn("failed", failed)
        for text in (ok, failed):
            self.assertNotIn(KEY, text)


def msg(text, *, user=123, chat_id=123, chat_type="private", uid=1, reply_to=None):
    body = {"chat": {"id": chat_id, "type": chat_type}, "from": {"id": user}, "text": text}
    if reply_to:
        body["reply_to_message"] = reply_to
    return {"update_id": uid, "message": body}


class PrivateChatTests(unittest.TestCase):
    def setUp(self):
        telegram_bot._SCOPES.clear()
        telegram_bot._HISTORY.clear()
        self.call = Mock()
        self.llm = Mock(return_value=None)

    def run_update(self, update, llm="default"):
        llm = self.llm if llm == "default" else llm
        with patch("telegram_bot.answer_intent", return_value=("answer", None)) as answer:
            result = process_update(update, Mock(), CONFIG, call=self.call, llm=llm)
        return result, answer

    def sent_to(self):
        return [c.args[2]["chat_id"] for c in self.call.call_args_list if c.args[1] == "sendMessage"]

    def test_allowlisted_user_can_chat_privately_without_commands_or_mentions(self):
        result, answer = self.run_update(msg("Golf under 15000 KM"))
        self.assertTrue(result["handled"])
        self.assertEqual([123], self.sent_to())  # the private chat, not the group
        answer.assert_called_once()

    def test_private_chat_access_cannot_be_spoofed(self):
        for update in (
            msg("hi", user=999, chat_id=999),                       # not allowlisted
            msg("hi", user=123, chat_id=999),                       # private chat belongs to someone else
            msg("hi", user=123, chat_id=123, chat_type="group"),    # not private
            msg("hi", user=123, chat_id=123, chat_type="supergroup"),
            msg("hi", user=123, chat_id=-100999, chat_type="supergroup"),
        ):
            result, answer = self.run_update(update)
            self.assertEqual("unauthorized", result["reason"], update)
            answer.assert_not_called()
        self.call.assert_not_called()
        self.llm.assert_not_called()

    def test_group_still_requires_an_address(self):
        group = msg("Golf under 15000 KM", chat_id=CONFIG.chat_id, chat_type="supergroup")
        result, _ = self.run_update(group)
        self.assertEqual("not_addressed", result["reason"])

    def test_gemini_is_only_used_when_the_parser_cannot_read_the_message(self):
        for text in ("Golf under 15000 KM", "status", "Golf vs Octavia", "what will prices be next year",
                     "golf under 10000 eur"):
            self.run_update(msg(text))
        self.llm.assert_not_called()
        with patch("telegram_bot.render_command", return_value="command answer"):
            self.run_update(msg("/status"))
            self.run_update(msg("/budget 25000 Golf"))
        self.llm.assert_not_called()
        self.run_update(msg("something sporty for my family please maybe"))
        self.llm.assert_called_once()
        self.assertEqual("something sporty for my family please maybe", self.llm.call_args.args[0])

    def test_gemini_failure_falls_back_to_the_normal_unsupported_reply(self):
        captured = []
        with patch("telegram_bot.answer_intent",
                   side_effect=lambda e, i, c: captured.append(i) or ("answer", None)):
            process_update(msg("xyz qwerty asdf zxcv lorem ipsum dolor"), Mock(), CONFIG,
                           call=self.call, llm=Mock(return_value=None))
            process_update(msg("something sporty for my family please maybe", uid=2), Mock(), CONFIG,
                           call=self.call, llm=Mock(return_value=None))
        self.assertEqual("unsupported", captured[0].kind)
        self.assertEqual("find", captured[1].kind)  # no Gemini answer: previous behaviour is kept

    def test_searches_with_a_filter_are_read_by_the_free_parser(self):
        for text in ("Golf from 2016", "golf 7 tdi under 12000 KM", "octavia max 150k km mileage"):
            self.run_update(msg(text))
        self.llm.assert_not_called()

    def test_bare_search_words_are_ambiguous_so_gemini_decides(self):
        for text in ("golf", "hvala ti", "bok"):
            self.run_update(msg(text))
        self.assertEqual(3, self.llm.call_count)

    def test_gemini_plans_are_answered_through_the_database_helpers(self):
        plan = Intent("find", Filters("golf", 15000, 2016))
        captured = []
        with patch("telegram_bot.answer_intent",
                   side_effect=lambda e, i, c: captured.append(i) or ("answer", i.filters)):
            result = process_update(msg("something sporty for my family please maybe"), Mock(), CONFIG,
                                    call=self.call, llm=Mock(return_value=plan))
        self.assertEqual(("find", plan), (result["command"], captured[0]))

    def test_chitchat_and_clarifying_question_replies(self):
        self.assertIn("used cars", telegram_bot.chitchat_message())
        text = telegram_bot.clarify_message("", "What budget do you have in mind?")
        self.assertTrue(text.startswith("What budget do you have in mind?"))
        self.assertIn("can't pick a car", text)

    def test_private_follow_ups_use_the_last_answer_but_fresh_questions_do_not(self):
        captured = []

        def fake(e, i, c):
            captured.append(i)
            return "answer", None

        with patch("telegram_bot.answer_intent", side_effect=fake):
            for uid, text in enumerate(("Golf under 15000 KM", "from 2018", "passat under 9000", "under 12000"), 1):
                process_update(msg(text, uid=uid), Mock(), CONFIG, call=self.call, llm=Mock(return_value=None))
        self.assertEqual(Filters("golf", 15000), captured[0].filters)
        self.assertEqual(Filters("golf", 15000, 2018), captured[1].filters)   # follow-up
        self.assertEqual(Filters("passat", 9000), captured[2].filters)        # fresh question
        self.assertEqual(Filters("passat", 12000), captured[3].filters)       # follow-up of the latest

    def test_memory_is_isolated_by_chat_and_user_and_expires(self):
        captured = []

        def fake(e, i, c):
            captured.append(i)
            return "answer", None

        with patch("telegram_bot.answer_intent", side_effect=fake):
            process_update(msg("Golf under 15000 KM", user=123, chat_id=123, uid=1), Mock(), CONFIG,
                           call=self.call, llm=Mock(return_value=None))
            # another allowlisted user in their own chat must not inherit it
            process_update(msg("from 2018", user=456, chat_id=456, uid=2), Mock(), CONFIG,
                           call=self.call, llm=Mock(return_value=None))
            # the group never uses memory either
            process_update(msg("@mojagaraza_bot from 2018", user=123, chat_id=CONFIG.chat_id,
                               chat_type="supergroup", uid=3), Mock(), CONFIG,
                           call=self.call, llm=Mock(return_value=None))
            self.assertEqual(Filters("", None, 2018), captured[1].filters)
            self.assertEqual(Filters("", None, 2018), captured[2].filters)
            # memory expires
            with patch("telegram_bot.time.monotonic", return_value=10 ** 9):
                process_update(msg("from 2018", user=123, chat_id=123, uid=4), Mock(), CONFIG,
                               call=self.call, llm=Mock(return_value=None))
            self.assertEqual(Filters("", None, 2018), captured[3].filters)

    def test_memory_is_bounded(self):
        for n in range(telegram_bot.SCOPE_MAX_ENTRIES + 20):
            telegram_bot._remember_scope((n, n), Intent("find", Filters("golf")))
        self.assertLessEqual(len(telegram_bot._SCOPES), telegram_bot.SCOPE_MAX_ENTRIES)

    def test_llm_command_reports_status_only_to_authorized_users(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": KEY}):
            with patch("telegram_llm.requests.post", return_value=reply(429, {})):
                process_update(msg("/llm", user=999, chat_id=999), Mock(), CONFIG, call=self.call)
                self.call.assert_not_called()
                result = process_update(msg("/llm"), Mock(), CONFIG, call=self.call)
        self.assertEqual("llm", result["command"])
        sent = self.call.call_args.args[2]["text"]
        self.assertIn("Gemini check failed", sent)
        self.assertNotIn(KEY, sent)
        process_update(msg("/llm", uid=9), Mock(), CONFIG, call=self.call)
        self.assertIn("not configured", self.call.call_args.args[2]["text"])

    def test_default_llm_is_off_without_a_key_and_on_with_one(self):
        default = telegram_bot._DEFAULT_LLM
        self.assertEqual((None, None), telegram_bot._resolve_llm(default, default))
        with patch.dict(os.environ, {"GEMINI_API_KEY": KEY}):
            interpreter, phraser = telegram_bot._resolve_llm(default, default)
            self.assertIsNotNone(interpreter)
            self.assertIsNotNone(phraser)
        explicit = Mock()
        self.assertEqual((explicit, None), telegram_bot._resolve_llm(explicit, None))


if __name__ == "__main__":
    unittest.main()
