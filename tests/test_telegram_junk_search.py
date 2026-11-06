"""Stray words must never become a 'car model'; refinement buttons edit in place."""

import os
import unittest
from unittest.mock import Mock, patch

import telegram_bot
from telegram_bot import Page, TelegramAPIError, TelegramConfig, find_message, find_page, process_update
from telegram_intent import Filters, Intent, parse_question
from telegram_llm import intent_from_plan
from test_telegram_questions import QuestionQueryTests

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
    for name in ("GEMINI_API_KEY", "TELEGRAM_DEFAULT_LANGUAGE"):
        os.environ.pop(name, None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def press(data, *, message_id=77, uid=1, user=123, chat_id=123):
    message = {"chat": {"id": chat_id, "type": "private"}}
    if message_id is not None:
        message["message_id"] = message_id
    return {"update_id": uid, "callback_query": {"id": "cb", "from": {"id": user}, "data": data, "message": message}}


def dm(text_value, *, uid=1):
    return {"update_id": uid, "message": {"chat": {"id": 123, "type": "private"}, "from": {"id": 123}, "text": text_value}}


class ParsingTests(unittest.TestCase):
    def search_and_budget(self, text_value):
        intent = parse_question(text_value)
        return intent.kind, intent.filters.search, intent.filters.budget

    def test_the_messages_from_the_bug_report_search_all_models_within_the_budget(self):
        for text_value in ("imam budzet. je 50000 KM", "Budzet je 50k", "50k budzet, je li dovoljno",
                           "budžet 50000 KM. dobar auto, jel ima nesto?", "Ćao, budžet je 50k, šta možeš da mi nađeš?",
                           "Cao! Imam 50k budzet. Je li to dovoljno za dobar auto?"):
            kind, search, budget = self.search_and_budget(text_value)
            self.assertEqual(("find", "", 50000), (kind, search, budget), text_value)

    def test_real_car_names_are_kept(self):
        for text_value, search in (("Golf under 15000 KM", "golf"), ("Škoda Octavia do 20000 KM", "škoda octavia"),
                                   ("bmw 320d do 25000", "bmw 320d"), ("audi a4 do 9000 KM", "audi a4"),
                                   ("golf 7 tdi do 12000 KM", "golf 7 tdi"), ("Golf, do 15000 KM.", "golf"),
                                   ("show me a Golf under 15000 KM please", "golf")):
            self.assertEqual(search, parse_question(text_value).filters.search, text_value)

    def test_punctuation_around_words_is_ignored(self):
        self.assertEqual("golf", parse_question("(golf) do 15000 KM!").filters.search)
        self.assertEqual("", parse_question("budzet... je. 20000 KM").filters.search)

    def test_a_very_long_sentence_is_not_a_search(self):
        for text_value in ("please could you maybe explain how olx works for foreigners in bosnia",
                           "ćao kako si danas imaš li možda vremena da mi pomogneš oko nečega"):
            self.assertEqual("unsupported", parse_question(text_value).kind, text_value)

    def test_the_planners_search_text_gets_the_same_cleaning(self):
        for raw, expected in (("budzet. je", ""), ("  Golf  ", "golf"), ("škoda octavia", "škoda octavia"),
                              ("golf; DROP TABLE x", "golf drop table x"), ("je li", "")):
            intent = intent_from_plan({"intent": "find", "search": raw, "budget_km": 20000})
            self.assertEqual(("find", expected), (intent.kind, intent.filters.search), raw)
        # a planner search that is only filler and no other filter is not a request at all
        self.assertEqual("clarify", intent_from_plan({"intent": "find", "search": "je li"}).kind)


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class UnknownSearchDatabaseTests(unittest.TestCase):
    setUpClass = classmethod(QuestionQueryTests.setUpClass.__func__)
    tearDownClass = classmethod(QuestionQueryTests.tearDownClass.__func__)
    setUp = QuestionQueryTests.setUp
    cleanup = QuestionQueryTests.cleanup
    seed = QuestionQueryTests.seed
    seed_many = QuestionQueryTests.seed_many

    def test_an_unrecognised_word_is_ignored_and_the_answer_says_so(self):
        self.seed_many()
        reply = find_message(self.engine, Filters("budzet. je", 20000))
        self.assertTrue(reply.startswith("Observed listings · up to 20.000 KM"))  # no junk in the title
        self.assertIn("I didn't recognise “budzet. je” as a car make or model, so I searched all models.", reply)
        self.assertIn("Showing 1–5 of 7", reply)
        bosnian = find_message(self.engine, Filters("budzet. je", 20000), lang="bs")
        self.assertTrue(bosnian.startswith("Viđeni oglasi · do 20.000 KM"))
        self.assertIn("Nisam prepoznao „budzet. je“ kao marku ili model, pa tražim među svim modelima.", bosnian)

    def test_the_buttons_carry_the_filters_that_were_really_used(self):
        self.seed_many()
        _, page = telegram_bot.answer_intent(self.engine, Intent("find", Filters("budzet. je", 20000)), CONFIG)
        self.assertEqual("", page.filters.search)
        data = [b["callback_data"] for row in telegram_bot.keyboard(CONFIG, page)["inline_keyboard"] for b in row
                if b.get("callback_data", "").startswith("f:")]
        self.assertTrue(data)
        self.assertTrue(all(":budzet" not in d for d in data), data)
        self.assertIn("f:20000:0:0:5:", data)  # show more, with no search

    def test_a_real_model_that_just_has_no_match_is_never_ignored(self):
        self.seed_many()
        reply = find_message(self.engine, Filters("golf", 100))  # Golf exists; nothing costs 100 KM
        self.assertIn("No matching listings", reply)
        self.assertNotIn("recognise", reply)
        self.assertTrue(reply.startswith("Observed listings · golf · up to 100 KM"))

    def test_a_search_with_no_other_filter_keeps_the_old_answer(self):
        self.seed_many()
        reply = find_message(self.engine, Filters("budzet"))
        self.assertIn("No matching listings", reply)
        self.assertIn("send /help", reply)
        self.assertNotIn("recognise", reply)

    def test_year_or_mileage_filters_also_trigger_the_fallback(self):
        self.seed_many()
        self.assertIn("recognise", find_message(self.engine, Filters("blah", None, 2015)))
        self.assertIn("recognise", find_message(self.engine, Filters("blah", None, None, 200000)))

    def test_paging_never_triggers_the_fallback(self):
        self.seed_many()
        self.assertIn("No more matches", find_message(self.engine, Filters("golf", 15000), 95))
        self.assertNotIn("recognise", find_message(self.engine, Filters("blah", 20000), 5))

    def test_the_search_stays_safe(self):
        self.seed_many()
        for hostile in ("%", "_", "'; DROP TABLE car_listings; --", "g_lf"):
            find_page(self.engine, Filters(hostile, 20000))
        self.assertGreater(len(find_page(self.engine, Filters("", 20000))[2]["items"]), 0)


class EditInPlaceTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS):
            store.clear()
        self.call = Mock(return_value={"message_id": 1})

    def run_update(self, update, **kw):
        page = Page(Filters("golf", 13500), 5, 2016, 100000, (("id1", 2016, 9000),))
        with patch("telegram_bot.answer_intent", return_value=("Rezultati", page)), \
                patch("telegram_bot.render_command", return_value="x"):
            return process_update(update, Mock(), CONFIG, call=self.call, llm=None, phraser=None)

    def methods(self):
        return [c.args[1] for c in self.call.call_args_list if c.args[1] not in {"answerCallbackQuery"}]

    def test_a_refinement_tap_edits_the_message_it_came_from(self):
        self.run_update(press("f:13500:0:0:0:golf"))
        self.assertEqual(["editMessageText"], self.methods())
        edit = [c for c in self.call.call_args_list if c.args[1] == "editMessageText"][0].args[2]
        self.assertEqual((123, 77, "Rezultati"), (edit["chat_id"], edit["message_id"], edit["text"]))
        self.assertIn("reply_markup", edit)

    def test_more_results_stay_a_new_message_so_page_one_is_not_lost(self):
        self.run_update(press("f:13500:0:0:5:golf"))
        self.assertEqual(["sendMessage"], self.methods())

    def test_a_failed_edit_sends_a_new_message_and_never_deletes_what_the_user_sees(self):
        def edit_fails(config, method, payload):
            if method == "editMessageText":
                raise TelegramAPIError("message is not modified")
            return {"message_id": 9}
        self.call = Mock(side_effect=edit_fails)
        self.run_update(press("f:13500:0:0:0:golf"))
        self.assertEqual(["editMessageText", "sendMessage"], self.methods())
        self.assertNotIn("deleteMessage", self.methods())

    def test_no_message_id_or_a_different_button_means_a_normal_new_message(self):
        self.run_update(press("f:13500:0:0:0:golf", message_id=None))
        self.assertEqual(["sendMessage"], self.methods())
        self.call.reset_mock()
        self.run_update(press("status", uid=2))
        self.assertEqual(["sendMessage"], self.methods())
        self.call.reset_mock()
        self.run_update(press("f:13500:0:0:0:golf", message_id="77", uid=3))  # not an int: ignore it
        self.assertEqual(["sendMessage"], self.methods())

    def test_the_looking_placeholder_is_still_removed_when_its_edit_fails(self):
        def edit_fails(config, method, payload):
            if method == "editMessageText":
                raise TelegramAPIError("x")
            return {"message_id": 5}
        self.call = Mock(side_effect=edit_fails)
        page = Page(Filters("golf", 13500), 5, 2016, 100000, ())
        with patch("telegram_bot.answer_intent", return_value=("Rezultati", page)):
            process_update(dm("Golf do 13500 KM"), Mock(), CONFIG, call=self.call, llm=Mock(return_value=None), phraser=None)
        self.assertEqual(["sendMessage", "editMessageText", "deleteMessage", "sendMessage"], self.methods())

    def test_strangers_cannot_edit_anything(self):
        result = self.run_update(press("f:13500:0:0:0:golf", user=999, chat_id=999))
        self.assertEqual("unauthorized", result["reason"])
        self.call.assert_not_called()


class ShowButtonTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS):
            store.clear()

    def labels(self, page):
        call = Mock(return_value={"message_id": 1})
        with patch("telegram_bot.answer_intent", return_value=("Rezultati", page)):
            process_update(dm("Golf do 15000 KM"), Mock(), CONFIG, call=call, llm=None,
                           phraser=Mock(return_value=("Nema ništa.", False)))
        payload = [c.args[2] for c in call.call_args_list if c.args[1] in {"sendMessage", "editMessageText"}][-1]
        return [b["text"] for row in (payload.get("reply_markup") or {"inline_keyboard": []})["inline_keyboard"] for b in row]

    def test_no_show_listings_button_when_there_is_nothing_to_show(self):
        labels = self.labels(Page(Filters("", 50000), None, None, None, ()))
        self.assertFalse(any(label.startswith("Pokaži oglase") for label in labels))
        self.assertIn("Budžet −10%", labels)  # the budget buttons still help

    def test_the_button_appears_when_there_are_listings(self):
        labels = self.labels(Page(Filters("golf", 15000), 5, 2012, 250000, (("id1", 2016, 9000),)))
        self.assertIn("Pokaži oglase (1–5)", labels)
        self.assertFalse(any(label.startswith("⭐") for label in labels))  # hidden listings, hidden stars


if __name__ == "__main__":
    unittest.main()
