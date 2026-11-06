import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from starlette.responses import Response

import dealer_api

from telegram_bot import (
    TelegramConfig,
    TelegramConfigurationError,
    command_for,
    help_message,
    keyboard,
    budget_message,
    compare_message,
    latest_message,
    process_update,
    status_message,
    trends_message,
)


ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
    "TELEGRAM_MARKET_URL": "https://cars.example/market",
}


class TelegramBotTests(unittest.TestCase):
    def test_configuration_is_explicit_and_fail_closed(self):
        config = TelegramConfig.from_env(ENV)
        self.assertEqual(-100123456789, config.chat_id)
        self.assertEqual(frozenset({123, 456}), config.allowed_user_ids)
        self.assertTrue(config.accepts_secret("test_only_webhook_secret_123"))
        self.assertFalse(config.accepts_secret("wrong"))
        with self.assertRaisesRegex(TelegramConfigurationError, "TELEGRAM_BOT_TOKEN"):
            TelegramConfig.from_env({})
        with self.assertRaisesRegex(TelegramConfigurationError, "integers"):
            TelegramConfig.from_env({**ENV, "TELEGRAM_CHAT_ID": "not-an-id"})
        with self.assertRaisesRegex(TelegramConfigurationError, "HTTPS"):
            TelegramConfig.from_env({**ENV, "TELEGRAM_MARKET_URL": "http://example.test"})

    def test_commands_are_bounded_and_unknown_text_returns_help(self):
        self.assertEqual(("status", ""), command_for("/status@CarBot"))
        self.assertEqual(("budget", "25000 Golf"), command_for("/budget 25000 Golf"))
        self.assertEqual(("trends", "30"), command_for("show trends for 30 days"))
        self.assertEqual(("latest", ""), command_for("show interesting finds"))
        self.assertEqual(("help", ""), command_for("invent a market prediction"))
        self.assertIn("Supabase evidence only", help_message())

    def test_keyboard_uses_callbacks_and_optional_https_market_link(self):
        config = TelegramConfig.from_env(ENV)
        markup = keyboard(config)
        flattened = [button for row in markup["inline_keyboard"] for button in row]
        self.assertIn("status", {button.get("callback_data") for button in flattened})
        self.assertIn("https://cars.example/market", {button.get("url") for button in flattened})

    def test_unauthorized_chat_or_user_is_silently_ignored(self):
        config = TelegramConfig.from_env(ENV)
        call = Mock()
        for chat_id, user_id in [(-100999, 123), (config.chat_id, 999)]:
            result = process_update({
                "update_id": 1,
                "message": {"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/help"},
            }, Mock(), config, call=call)
            self.assertEqual("unauthorized", result["reason"])
        call.assert_not_called()

    def test_authorized_help_and_callback_send_only_to_configured_group(self):
        config = TelegramConfig.from_env(ENV)
        calls = []

        def call(_config, method, payload):
            calls.append((method, payload))

        update = {
            "update_id": 2,
            "message": {
                "chat": {"id": config.chat_id},
                "from": {"id": 123},
                "text": "/help",
            },
        }
        result = process_update(update, Mock(), config, call=call)
        self.assertTrue(result["handled"])
        self.assertEqual("sendMessage", calls[0][0])
        self.assertEqual(config.chat_id, calls[0][1]["chat_id"])
        self.assertNotIn(config.bot_token, calls[0][1]["text"])

        calls.clear()
        callback = {
            "update_id": 3,
            "callback_query": {
                "id": "callback-test",
                "from": {"id": 123},
                "data": "trends:7",
                "message": {"chat": {"id": config.chat_id}},
            },
        }
        with patch("telegram_bot.render_command", return_value="database result") as render:
            result = process_update(callback, Mock(), config, call=call)
        self.assertTrue(result["handled"])
        render.assert_called_once()
        self.assertEqual(["answerCallbackQuery", "sendMessage"], [method for method, _ in calls])

    def test_production_middleware_opens_only_webhook_post(self):
        async def call_next(_request):
            return Response(status_code=204)

        def request(method):
            return SimpleNamespace(
                method=method,
                url=SimpleNamespace(path=dealer_api.TELEGRAM_WEBHOOK_PATH),
                headers={},
            )

        with patch.object(dealer_api, "HOSTED", True), \
             patch.dict(os.environ, {"VERCEL_ENV": "production"}):
            post = asyncio.run(dealer_api.private_requests(request("POST"), call_next))
            get = asyncio.run(dealer_api.private_requests(request("GET"), call_next))
        self.assertEqual(204, post.status_code)
        self.assertEqual(503, get.status_code)


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class TelegramQuerySmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_all_database_commands_execute_against_current_schema(self):
        self.assertIn("no finished collector run", status_message(self.engine))
        self.assertIn("last 7 days", trends_message(self.engine, 7))
        self.assertIn("No matching listings", budget_message(self.engine, "25000 Golf"))
        self.assertIn("Database comparison", compare_message(
            self.engine, "Volkswagen Golf | Škoda Octavia"))
        self.assertIn("No collector run", latest_message(self.engine))


if __name__ == "__main__":
    unittest.main()
