#!/usr/bin/env python3
"""Inspect or configure Car Analyzer's Telegram bot without storing its token."""

import argparse
import json
import os
import sys
from urllib.parse import urlparse

import requests


API = "https://api.telegram.org"


class SetupError(RuntimeError):
    pass


def token():
    value = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not value:
        raise SetupError("TELEGRAM_BOT_TOKEN is missing from the process environment")
    return value


def call(method, payload=None):
    try:
        response = requests.post(
            f"{API}/bot{token()}/{method}", json=payload or {}, timeout=15)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SetupError(f"Telegram {method} request failed") from exc
    if not data.get("ok"):
        raise SetupError(f"Telegram {method} returned an error")
    return data.get("result")


def inspect_bot():
    bot = call("getMe")
    webhook = call("getWebhookInfo")
    return {
        "bot": {
            "id": bot.get("id"),
            "username": bot.get("username"),
            "can_join_groups": bot.get("can_join_groups"),
            "can_read_all_group_messages": bot.get("can_read_all_group_messages"),
        },
        "webhook": {
            "configured": bool(webhook.get("url")),
            "url": webhook.get("url") or None,
            "pending_update_count": webhook.get("pending_update_count"),
            "last_error_date": webhook.get("last_error_date"),
            "last_error_message": webhook.get("last_error_message"),
        },
    }


def discover_ids():
    webhook = call("getWebhookInfo")
    if webhook.get("url"):
        raise SetupError("ID discovery requires no active webhook; inspect or delete it first")
    updates = call("getUpdates", {"timeout": 0, "allowed_updates": ["message"]})
    found = {}
    for update in updates:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if chat.get("id") is None or sender.get("id") is None:
            continue
        key = (chat["id"], sender["id"])
        found[key] = {
            "chat_id": chat["id"],
            "chat_type": chat.get("type"),
            "chat_title": chat.get("title"),
            "user_id": sender["id"],
            "username": sender.get("username"),
        }
    return list(found.values())


def set_webhook(url, confirmation):
    secret = (os.environ.get("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    if confirmation != "SET_WEBHOOK":
        raise SetupError("Setting the webhook requires --confirm SET_WEBHOOK")
    if not secret:
        raise SetupError("TELEGRAM_WEBHOOK_SECRET is missing from the process environment")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path != "/api/telegram/webhook":
        raise SetupError("Webhook URL must be HTTPS and end exactly at /api/telegram/webhook")
    result = call("setWebhook", {
        "url": url,
        "secret_token": secret,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
        "max_connections": 10,
    })
    return {"configured": bool(result), "url": url, "allowed_updates": ["message", "callback_query"]}


def delete_webhook(confirmation):
    if confirmation != "DELETE_WEBHOOK":
        raise SetupError("Deleting the webhook requires --confirm DELETE_WEBHOOK")
    return {"deleted": bool(call("deleteWebhook", {"drop_pending_updates": False}))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inspect")
    subparsers.add_parser("discover-ids")
    configure = subparsers.add_parser("set-webhook")
    configure.add_argument("--url", required=True)
    configure.add_argument("--confirm")
    remove = subparsers.add_parser("delete-webhook")
    remove.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_bot()
        elif args.command == "discover-ids":
            result = discover_ids()
        elif args.command == "set-webhook":
            result = set_webhook(args.url, args.confirm)
        else:
            result = delete_webhook(args.confirm)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
