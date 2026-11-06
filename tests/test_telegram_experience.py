"""Conversation experience: language, contextual buttons, the 'looking…' placeholder."""

from datetime import datetime, timedelta, timezone
import json
import os
import re
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

import telegram_bot
import telegram_i18n
import telegram_llm
from telegram_bot import (
    Choices, Page, TelegramConfig, answer_intent, budget_choices, example_choices, find_message,
    keyboard, parse_suggest_callback, process_update, status_message, suggest_callback, suggest_message,
    trends_message,
)
from telegram_i18n import STRINGS, detect_language, plural_bs, t, telegram_language
from telegram_intent import Filters, Intent, context_from_reply
from test_telegram_questions import QuestionQueryTests

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
    "TELEGRAM_MARKET_URL": "https://cars.example/market",
}
CONFIG = TelegramConfig.from_env(ENV)
PLAIN = TelegramConfig.from_env({k: v for k, v in ENV.items() if k != "TELEGRAM_MARKET_URL"})


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    for name in ("GEMINI_API_KEY", "TELEGRAM_DEFAULT_LANGUAGE"):
        os.environ.pop(name, None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def dm(text_value, *, user=123, chat_id=123, uid=1, language_code=None, reply_to=None):
    sender = {"id": user}
    if language_code:
        sender["language_code"] = language_code
    body = {"chat": {"id": chat_id, "type": "private"}, "from": sender, "text": text_value}
    if reply_to:
        body["reply_to_message"] = reply_to
    return {"update_id": uid, "message": body}


def press(data, *, user=123, chat_id=123, uid=1, language_code=None):
    sender = {"id": user}
    if language_code:
        sender["language_code"] = language_code
    return {"update_id": uid, "callback_query": {
        "id": "cb", "from": sender, "data": data, "message": {"chat": {"id": chat_id, "type": "private"}}}}


def last_text(call):
    return [c.args[2]["text"] for c in call.call_args_list if c.args[1] in {"sendMessage", "editMessageText"}][-1]


def last_payload(call):
    return [c.args[2] for c in call.call_args_list if c.args[1] in {"sendMessage", "editMessageText"}][-1]


class StringTableTests(unittest.TestCase):
    def fields(self, template):
        return sorted(set(re.findall(r"{([a-z_0-9]+)}", template)))

    def test_every_english_string_has_a_bosnian_twin_with_the_same_placeholders(self):
        english, bosnian = STRINGS["en"], STRINGS["bs"]
        self.assertEqual(set(english), set(bosnian))
        for key, template in english.items():
            self.assertEqual(self.fields(template), self.fields(bosnian[key]), key)

    def test_every_string_formats_with_its_own_fields(self):
        for lang in ("en", "bs"):
            for key, template in STRINGS[lang].items():
                values = {name: "x" for name in self.fields(template)}
                self.assertIsInstance(t(lang, key, **values) if values else t(lang, key), str)

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(t("en", "no_more"), t("xx", "no_more"))
        self.assertEqual(t("en", "no_more"), t(None, "no_more"))

    def test_bosnian_plural_forms(self):
        for number, expected in ((1, "dan"), (2, "dana"), (4, "dana"), (5, "dana"), (11, "dana"), (21, "dan"),
                                 (22, "dana"), (25, "dana")):
            self.assertEqual(expected, plural_bs(number, "dan", "dana", "dana"), number)
        self.assertEqual("sedmicu", plural_bs(1, "sedmicu", "sedmice", "sedmica"))
        self.assertEqual("sedmice", plural_bs(3, "sedmicu", "sedmice", "sedmica"))
        self.assertEqual("sedmica", plural_bs(12, "sedmicu", "sedmice", "sedmica"))

    def test_language_is_detected_from_the_message(self):
        for text_value, expected in (
            ("imam 50k budzet, sta da kupim", "bs"), ("Golf do 15000 KM", "bs"), ("hvala ti", "bs"),
            ("koliko košta passat", "bs"), ("šta ima novo", "bs"), ("what car should I buy", "en"),
            ("Golf under 15000 KM", "en"), ("thanks a lot", "en"), ("show me the latest", "en"),
            ("follow zzzz", "en"), ("prati golf", "bs"), ("golf", None), ("", None), ("12345", None), ("Golf 7", None),
        ):
            self.assertEqual(expected, detect_language(text_value), text_value)

    def test_telegram_app_language_is_mapped(self):
        self.assertEqual("bs", telegram_language("bs"))
        self.assertEqual("bs", telegram_language("hr"))
        self.assertEqual("bs", telegram_language("sr-Latn"))
        self.assertEqual("en", telegram_language("en-US"))
        self.assertIsNone(telegram_language(None))
        self.assertIsNone(telegram_language(""))


class ReplyLanguageTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS, telegram_bot._ACTIVE):
            store.clear()
        self.call = Mock(return_value={"message_id": 1})

    def test_replies_follow_the_language_of_the_message(self):
        with patch("telegram_bot.answer_intent", return_value=("x", False)):
            process_update(dm("hvala ti"), Mock(), CONFIG, call=self.call, llm=None, phraser=None)
        process_update(dm("/help", uid=2), Mock(), CONFIG, call=self.call, llm=None, phraser=None)
        self.assertIn("čita samo naše podatke", last_text(self.call))  # remembered from the Bosnian message

    def test_english_stays_english(self):
        process_update(dm("/help", uid=1), Mock(), CONFIG, call=self.call, llm=None, phraser=None)
        self.assertIn("reads our Supabase evidence only", last_text(self.call))

    def test_buttons_and_commands_use_the_remembered_language(self):
        with patch("telegram_bot.answer_intent", wraps=telegram_bot.answer_intent) as answered, \
                patch("telegram_bot.find_page", return_value=("Viđeni oglasi · golf", 20, {})):
            process_update(dm("Golf do 15000 KM", uid=1), Mock(), CONFIG, call=self.call, llm=None, phraser=None)
            process_update(press("f:15000:0:0:5:golf", uid=2), Mock(), CONFIG, call=self.call, llm=None, phraser=None)
        self.assertEqual("bs", answered.call_args.kwargs["lang"])

    def test_telegram_app_language_is_the_fallback_then_the_default(self):
        with patch("telegram_bot.render_command", return_value="x") as render:
            process_update(dm("/status", user=456, chat_id=456, language_code="bs"), Mock(), CONFIG,
                           call=self.call, llm=None, phraser=None)
            self.assertEqual("bs", render.call_args.args[3])
            process_update(dm("/status", user=123, chat_id=123, uid=2, language_code="en"), Mock(), CONFIG,
                           call=self.call, llm=None, phraser=None)
            self.assertEqual("en", render.call_args.args[3])
            process_update(dm("/status", user=123, chat_id=123, uid=3), Mock(), CONFIG, call=self.call,
                           llm=None, phraser=None)
            self.assertEqual("en", render.call_args.args[3])  # remembered English from before
            telegram_bot._LANGS.clear()
            with patch.dict(os.environ, {"TELEGRAM_DEFAULT_LANGUAGE": "bs"}):
                process_update(dm("/status", user=123, chat_id=123, uid=4), Mock(), CONFIG, call=self.call,
                               llm=None, phraser=None)
            self.assertEqual("bs", render.call_args.args[3])

    def test_language_memory_is_per_person_bounded_and_expires(self):
        telegram_bot._remember_language((1, 1), "bs")
        self.assertEqual("bs", telegram_bot.resolve_language((1, 1), "", {}))
        self.assertEqual("en", telegram_bot.resolve_language((2, 2), "", {}))
        with patch("telegram_bot.time.monotonic", return_value=10 ** 9):
            self.assertEqual("en", telegram_bot.resolve_language((1, 1), "", {}))
        for n in range(telegram_bot.SCOPE_MAX_ENTRIES + 20):
            telegram_bot._remember_language((n, n), "bs")
        self.assertLessEqual(len(telegram_bot._LANGS), telegram_bot.SCOPE_MAX_ENTRIES)
        telegram_bot._remember_language((9, 9), "xx")
        self.assertNotIn((9, 9), telegram_bot._LANGS)

    def test_database_errors_are_reported_in_the_users_language(self):
        error = OperationalError("SELECT 1", {}, Exception("secret"))
        with patch("telegram_bot.answer_intent", side_effect=error):
            process_update(dm("Golf do 15000 KM"), Mock(), CONFIG, call=self.call, llm=None, phraser=None)
            self.assertEqual(t("bs", "db_unavailable"), last_text(self.call))
            process_update(dm("Golf under 15000 KM", uid=2, user=456, chat_id=456), Mock(), CONFIG,
                           call=self.call, llm=None, phraser=None)
            self.assertEqual(t("en", "db_unavailable"), last_text(self.call))
        self.assertIsNone(last_payload(self.call).get("reply_markup"))

    def test_gemini_is_told_the_language_in_both_calls(self):
        post = Mock()
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": json.dumps({"intent": "chitchat", "reply": "Zdravo!"})}]}}]}
        cfg = telegram_llm.LLMConfig("key-0123456789", "gemini-3.5-flash-lite")
        telegram_llm.interpret("bok", None, cfg, post=post, language="bs")
        sent = json.loads(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertEqual("Bosnian", sent["language"])
        post.return_value.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": json.dumps({"reply": "Evo.", "show_details": False})}]}}]}
        telegram_llm.phrase("x", "facts", cfg, post=post, language="en")
        sent = json.loads(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertEqual("English", sent["language"])
        telegram_llm.phrase("x", "facts", cfg, post=post)
        self.assertNotIn("language", json.loads(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]))
        self.assertIn('"language" field', post.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"])

    def test_bosnian_titles_still_work_as_reply_context(self):
        context = context_from_reply("Viđeni oglasi · golf · do 15.000 KM · od 2015\nPrikazujem 1–5 od 7")
        self.assertEqual(Filters("golf", 15000, 2015), context.filters)
        bot_msg = {"from": {"id": 9, "is_bot": True}, "text": "Viđeni oglasi · golf · do 15.000 KM"}
        captured = []
        with patch("telegram_bot.answer_intent", side_effect=lambda e, i, c, **kw: captured.append(i) or ("x", False)):
            process_update(dm("od 2018", reply_to=bot_msg), Mock(), CONFIG, call=self.call, llm=None, phraser=None)
        self.assertEqual(Filters("golf", 15000, 2018), captured[0].filters)


class ContextualButtonTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS, telegram_bot._ACTIVE):
            store.clear()

    def rows(self, page, config=PLAIN, lang="en"):
        markup = keyboard(config, page, lang)
        return None if markup is None else [[b["text"] for b in row] for row in markup["inline_keyboard"]]

    def callbacks(self, page, config=PLAIN):
        return [b["callback_data"] for row in keyboard(config, page)["inline_keyboard"] for b in row if "callback_data" in b]

    def test_search_results_offer_more_budget_newer_and_lower_mileage(self):
        page = Page(Filters("golf", 15000), next_offset=5, min_year=2012, max_km=250000)
        rows = self.rows(page)
        self.assertEqual("Show more (6–10)", rows[0][0])
        self.assertEqual(["Budget −10%", "Budget +10%"], rows[1])
        self.assertEqual(["📅 From 2014", "🛣 Up to 170.000 km"], rows[2])
        self.assertNotIn("Status", [b for row in rows for b in row])  # no generic navigation row

    def test_hidden_results_get_a_show_button_for_page_one(self):
        rows = self.rows(Page(Filters("golf", 15000), next_offset=0))
        self.assertEqual("Show listings (1–5)", rows[0][0])
        self.assertEqual("f:15000:0:0:0:golf", self.callbacks(Page(Filters("golf", 15000), next_offset=0))[0])
        self.assertEqual("Pokaži oglase (1–5)", self.rows(Page(Filters("golf", 15000), next_offset=0), lang="bs")[0][0])

    def test_a_phrased_answer_without_listings_points_the_button_at_page_one(self):
        call = Mock(return_value={"message_id": 1})
        page = Page(Filters("golf", 15000), next_offset=5, min_year=2012, max_km=250000, items=(("id1", 2016, 9000),))
        with patch("telegram_bot.answer_intent", return_value=("Rezultati", page)):
            process_update(dm("Golf do 15000 KM"), Mock(), CONFIG, call=call, llm=None,
                           phraser=Mock(return_value=("Našao sam ih. Hoćeš da vidiš?", False)))
        first_button = last_payload(call)["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(("Pokaži oglase (1–5)", "f:15000:0:0:0:golf"), (first_button["text"], first_button["callback_data"]))
        call = Mock(return_value={"message_id": 1})
        with patch("telegram_bot.answer_intent", return_value=("Rezultati", page)):
            process_update(dm("Golf do 15000 KM", uid=2), Mock(), CONFIG, call=call, llm=None,
                           phraser=Mock(return_value=("Evo.", True)))
        labels = [b["text"] for row in last_payload(call)["reply_markup"]["inline_keyboard"] for b in row]
        self.assertIn("Još rezultata (6–10)", labels)
        self.assertNotIn("Pokaži oglase (1–5)", labels)

    def test_the_same_buttons_in_bosnian(self):
        page = Page(Filters("golf", 15000), next_offset=5, min_year=2012, max_km=250000)
        rows = self.rows(page, lang="bs")
        self.assertEqual(["Još rezultata (6–10)"], rows[0])
        self.assertEqual(["Budžet −10%", "Budžet +10%"], rows[1])
        self.assertEqual(["📅 Od 2014", "🛣 Do 170.000 km"], rows[2])

    def test_a_search_without_a_budget_offers_budget_chips(self):
        rows = self.rows(Page(Filters("golf")))
        self.assertEqual(["10.000 KM", "20.000 KM", "30.000 KM"], rows[0])

    def test_newer_and_lower_mileage_move_in_sensible_steps(self):
        steps = self.callbacks(Page(Filters("golf", 15000, 2016, 120000)))
        self.assertIn("f:15000:2018:120000:0:golf", steps)
        self.assertIn("f:15000:2016:80000:0:golf", steps)
        this_year = datetime.now(timezone.utc).year
        capped = self.callbacks(Page(Filters("golf", 15000, this_year)))
        self.assertFalse(any(c.startswith(f"f:15000:{this_year + 1}") for c in capped))

    def test_no_buttons_are_offered_that_would_change_nothing(self):
        rows = self.rows(Page(Filters("golf", 15000, datetime.now(timezone.utc).year)))
        self.assertNotIn("📅", " ".join(b for row in rows for b in row))

    def test_the_market_link_is_kept_on_search_results_only_when_configured(self):
        with_link = keyboard(CONFIG, Page(Filters("golf", 15000)))["inline_keyboard"]
        self.assertEqual({"text": "Open Market", "url": "https://cars.example/market"}, with_link[-1][0])
        self.assertFalse(any("url" in b for row in keyboard(PLAIN, Page(Filters("golf", 15000)))["inline_keyboard"]
                             for b in row))

    def test_every_button_fits_telegrams_64_byte_limit(self):
        pages = [Page(Filters("x" * 40, 999_000, 2030, 999_000), 95, 1980, 999_999),
                 Page(Filters("škoda octavia combi", 15000, 2016, 120000), 5, 2010, 300000)]
        for page in pages:
            for row in (keyboard(CONFIG, page) or {"inline_keyboard": []})["inline_keyboard"]:
                for button in row:
                    if "callback_data" in button:
                        self.assertLessEqual(len(button["callback_data"].encode()), 64, button)

    def test_small_talk_gets_no_buttons_and_others_get_choices(self):
        self.assertIsNone(keyboard(CONFIG, False))
        _, markup = answer_intent(Mock(), Intent("chitchat", reply="Nema na čemu!"), CONFIG, lang="bs")
        self.assertIs(False, markup)
        text_value, markup = answer_intent(Mock(), Intent("clarify", reply="Koji ti je budžet?"), CONFIG, lang="bs")
        self.assertEqual(["10.000 KM", "20.000 KM", "30.000 KM", "50.000 KM"], self.rows(markup)[0])
        self.assertEqual(["s:10000", "s:20000", "s:30000", "s:50000"], self.callbacks(markup))
        _, markup = answer_intent(Mock(), Intent("clarify", note="currency"), CONFIG)
        self.assertEqual(["🔎 Golf under 15000 KM", "💡 What should I buy for 30000 KM", "🆕 What's new"],
                         self.rows(markup)[0])
        _, markup = answer_intent(Mock(), Intent("unsupported"), CONFIG, lang="bs")
        self.assertEqual(["🔎 Golf do 15000 KM", "💡 Šta da kupim do 30000 KM", "🆕 Šta je novo"], self.rows(markup)[0])

    def test_suggestion_budget_buttons_validate_their_data(self):
        self.assertEqual(30000, parse_suggest_callback(suggest_callback(30000)))
        for bad in ("s:", "s:abc", "s:5", "s:99999999", "x:30000", None, "s:-1"):
            self.assertIsNone(parse_suggest_callback(bad), bad)

    def test_tapping_a_budget_chip_runs_the_shortlist(self):
        captured = []
        with patch("telegram_bot.answer_intent", side_effect=lambda e, i, c, **kw: captured.append(i) or ("x", False)):
            result = process_update(press("s:30000"), Mock(), CONFIG, call=Mock(), llm=None, phraser=None)
        self.assertEqual(("suggest", Filters("", 30000)), (result["command"], captured[0].filters))
        with patch("telegram_bot.answer_intent") as answer:
            process_update(press("s:5"), Mock(), CONFIG, call=Mock(), llm=None, phraser=None)
        self.assertEqual("help", answer.call_args.args[1].kind if answer.call_args else "help")

    def test_commands_keep_the_general_navigation_buttons(self):
        for command in ("/status", "/latest", "/trends 7", "/help"):
            call = Mock(return_value={"message_id": 1})
            with patch("telegram_bot.render_command", return_value="answer"):
                process_update(dm(command), Mock(), CONFIG, call=call, llm=None, phraser=None)
            labels = [b["text"] for row in last_payload(call)["reply_markup"]["inline_keyboard"] for b in row]
            self.assertIn("Status", labels, command)

    def test_start_shows_a_welcome_with_example_chips(self):
        call = Mock(return_value={"message_id": 1})
        process_update(dm("/start", language_code="bs"), Mock(), CONFIG, call=call, llm=None, phraser=None)
        payload = last_payload(call)
        self.assertTrue(payload["text"].startswith("Zdravo! Ja sam Garaža."))
        self.assertEqual(3, len(payload["reply_markup"]["inline_keyboard"][0]))

    def test_the_example_chips_actually_run(self):
        for _label, data in example_choices("en").rows[0]:
            self.assertTrue(data == "latest" or parse_suggest_callback(data) or telegram_bot.parse_refine_page(data), data)
        for _label, data in budget_choices("en").rows[0]:
            self.assertIsNotNone(parse_suggest_callback(data))

    def test_search_answers_pass_the_hints_to_the_buttons(self):
        with patch("telegram_bot.find_page", return_value=("text", 20, {"min_year": 2012, "max_km": 250000})):
            _, page = answer_intent(Mock(), Intent("find", Filters("golf", 15000)), CONFIG)
        self.assertEqual((5, 2012, 250000), (page.next_offset, page.min_year, page.max_km))
        with patch("telegram_bot.model_stats_message", return_value="t"):
            _, choices = answer_intent(Mock(), Intent("model_stats", Filters("octavia")), CONFIG, lang="bs")
        self.assertEqual("🔎 Pokaži oglase", choices.rows[0][0][0])
        self.assertEqual("f:0:0:0:0:octavia", choices.rows[0][0][1])
        with patch("telegram_bot.compare_message", return_value="t"):
            _, choices = answer_intent(Mock(), Intent("compare", labels=("golf", "octavia")), CONFIG)
        self.assertEqual(["🔎 golf", "🔎 octavia"], [label for label, _ in choices.rows[0]])
        with patch("telegram_bot.suggest_message", return_value=("t", ["Volkswagen Golf", "Škoda Octavia", "Audi A4", "BMW 3"])):
            _, choices = answer_intent(Mock(), Intent("suggest", Filters("", 30000, 2015)), CONFIG)
        self.assertEqual(3, len(choices.rows[0]))
        self.assertEqual("f:30000:2015:0:0:Volkswagen Golf", choices.rows[0][0][1])


class PlaceholderTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS, telegram_bot._ACTIVE):
            store.clear()

    def test_a_looking_message_is_sent_first_and_replaced_by_the_answer(self):
        call = Mock(return_value={"message_id": 42})
        with patch("telegram_bot.answer_intent", return_value=("Rezultati", Page(Filters("golf", 15000)))):
            process_update(dm("Golf do 15000 KM"), Mock(), CONFIG, call=call, llm=Mock(return_value=None),
                           phraser=Mock(return_value=None))
        first, last = call.call_args_list[0], call.call_args_list[-1]
        self.assertEqual(("sendMessage", "🔎 Tražim…"), (first.args[1], first.args[2]["text"]))
        self.assertEqual(("editMessageText", 42, "Rezultati"), (last.args[1], last.args[2]["message_id"],
                                                               last.args[2]["text"]))
        self.assertIn("reply_markup", last.args[2])
        self.assertEqual(2, len(call.call_args_list))

    def test_english_placeholder(self):
        call = Mock(return_value={"message_id": 1})
        with patch("telegram_bot.answer_intent", return_value=("Results", False)):
            process_update(dm("Golf under 15000 KM"), Mock(), CONFIG, call=call, llm=Mock(return_value=None),
                           phraser=None)
        self.assertEqual("🔎 Looking…", call.call_args_list[0].args[2]["text"])

    def test_no_placeholder_without_gemini_or_for_commands_and_buttons(self):
        for update in (dm("Golf under 15000 KM"), dm("/status", uid=2), press("status", uid=3)):
            call = Mock(return_value={"message_id": 1})
            with patch("telegram_bot.answer_intent", return_value=("x", False)), \
                    patch("telegram_bot.render_command", return_value="x"):
                process_update(update, Mock(), CONFIG, call=call, llm=None, phraser=None)
            sends = [c.args[1] for c in call.call_args_list if c.args[1] in {"sendMessage", "editMessageText"}]
            self.assertEqual(["sendMessage"], sends)
        call = Mock(return_value={"message_id": 1})
        with patch("telegram_bot.render_command", return_value="x"):
            process_update(dm("/status", uid=4), Mock(), CONFIG, call=call, llm=Mock(), phraser=Mock())
        self.assertEqual(["sendMessage"], [c.args[1] for c in call.call_args_list])

    def test_a_thank_you_removes_the_looking_message_when_gemini_stays_silent(self):
        call = Mock(return_value={"message_id": 8})
        process_update(dm("thanks"), Mock(), CONFIG, call=call, llm=Mock(return_value=None), phraser=None)
        methods = [c.args[1] for c in call.call_args_list]
        self.assertEqual(["sendMessage", "deleteMessage"], methods)
        self.assertEqual(8, call.call_args_list[-1].args[2]["message_id"])

    def test_unauthorized_senders_get_no_placeholder(self):
        call = Mock()
        result = process_update(dm("hello", user=999, chat_id=999), Mock(), CONFIG, call=call, llm=Mock(), phraser=Mock())
        self.assertEqual("unauthorized", result["reason"])
        call.assert_not_called()


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class BosnianAnswerDbTests(unittest.TestCase):
    PREFIX = "__tg_"
    setUpClass = classmethod(QuestionQueryTests.setUpClass.__func__)
    tearDownClass = classmethod(QuestionQueryTests.tearDownClass.__func__)
    setUp = QuestionQueryTests.setUp
    cleanup = QuestionQueryTests.cleanup
    seed = QuestionQueryTests.seed
    seed_many = QuestionQueryTests.seed_many
    seed_suggest = QuestionQueryTests.seed_suggest

    def test_search_results_in_bosnian(self):
        self.seed_many()
        reply = find_message(self.engine, Filters("golf", 15000), lang="bs")
        self.assertTrue(reply.startswith("Viđeni oglasi · golf · do 15.000 KM"))
        self.assertIn("Prikazujem 1–5 od 7 rezultata iz baze", reply)
        self.assertIn("Viđeno juče", reply)
        self.assertIn("Ovo su viđene tražene cijene", reply)
        self.assertNotIn("Showing", reply)
        self.assertIn("Nema više rezultata.", find_message(self.engine, Filters("golf", 15000), 95, lang="bs"))
        self.assertIn("Nema odgovarajućih oglasa", find_message(self.engine, Filters("ferrari"), lang="bs"))

    def test_old_sightings_are_flagged_in_bosnian(self):
        self.seed_suggest()
        reply = find_message(self.engine, Filters("golf", 8500), lang="bs")
        self.assertIn("⚠ Viđeno prije 4 sedmice", reply)
        self.assertIn("oglas možda više nije aktivan", reply)

    def test_shortlist_in_bosnian(self):
        self.seed_suggest()
        reply = suggest_message(self.engine, Filters("", 20000), lang="bs")
        self.assertTrue(reply.startswith("Uža lista na osnovu podataka · do 20.000 KM"))
        self.assertIn("• Volkswagen Golf · 6 oglasa · medijan 12.250 KM · medijan 165.000 km · najnovije 2016", reply)
        self.assertIn("21% ispod medijana od 12.500 KM, 7 sličnih", reply)
        self.assertIn("nemamo podatke o prodaji", reply)

    def test_status_trends_and_model_prices_in_bosnian(self):
        self.seed()
        status = status_message(self.engine, "bs")
        self.assertIn("Car Analyzer · ZAVRŠENO", status)
        self.assertIn("10 zapažanja", status)
        self.assertIn("Nestanak iz pretrage nije dokaz da je auto prodan.", status)
        self.assertIn("Zabilježene promjene tražene cijene · zadnjih 7 dana", trends_message(self.engine, 7, "bs"))
        stats = telegram_bot.model_stats_message(self.engine, "golf", "bs")
        self.assertIn("Tražene cijene · golf", stats)
        self.assertIn("Medijan tražene cijene:", stats)
        self.assertIn("Mali uzorak", stats)
        compare = telegram_bot.compare_message(self.engine, "golf | skoda octavia", "bs")
        self.assertIn("Poređenje iz baze", compare)
        latest = telegram_bot.latest_message(self.engine, "bs")
        self.assertIn("Zadnji prolaz sa zapažanjima · ZAVRŠENO", latest)

    def test_empty_and_stale_data_are_stated_in_bosnian(self):
        self.assertIn("Baza još nema pohranjenih zapažanja", find_message(self.engine, Filters("golf"), lang="bs"))
        self.assertIn("još nema nijedan završen prolaz", status_message(self.engine, "bs"))
        self.seed_stale_run()
        stale = find_message(self.engine, Filters("golf"), lang="bs")
        self.assertIn("Zastarjeli podaci", stale)
        self.assertIn("DJELIMIČNO", stale)

    def seed_stale_run(self):
        self.seed(observed_hours_ago=24 * 6, run_status="partial")

    def test_english_output_is_unchanged(self):
        self.seed_suggest()
        reply = find_message(self.engine, Filters("golf", 8500))
        self.assertIn("Showing 1 of 1 database matches", reply)
        self.assertIn("⚠ Seen 4 weeks ago", reply)
        self.assertEqual(find_message(self.engine, Filters("golf", 8500)),
                         find_message(self.engine, Filters("golf", 8500), lang="en"))


class AgeAndDateTests(unittest.TestCase):
    def test_bosnian_ages_and_dates(self):
        now = datetime.now(timezone.utc)
        for days, expected in ((0, "danas"), (1, "juče"), (3, "prije 3 dana"), (13, "prije 13 dana"),
                               (14, "prije 2 sedmice"), (35, "prije 5 sedmica"), (59, "prije 8 sedmica"),
                               (65, "prije 2 mjeseca"), (150, "prije 5 mjeseci")):
            self.assertEqual(expected, telegram_bot._age(now - timedelta(days=days, minutes=1), "bs"), days)
        self.assertEqual("u nepoznato vrijeme", telegram_bot._age(None, "bs"))
        stamp = datetime(2026, 11, 5, 12, 15, tzinfo=timezone.utc)
        self.assertEqual("5. nov, 13:15", telegram_bot._time(stamp, "bs"))
        self.assertEqual("05 Nov, 13:15", telegram_bot._time(stamp, "en"))
        self.assertEqual("nepoznato", telegram_bot._time(None, "bs"))
        self.assertEqual("cijena nije dostupna", telegram_bot._money(None, "bs"))
        self.assertEqual("12.500 KM", telegram_bot._money(12500, "bs"))


if __name__ == "__main__":
    unittest.main()
