# Next: arm the Garaža morning digest

The digest code is in the repo and is **not live**. Nothing here starts the
collector, changes a database role, cancels DigitalOcean, or registers a Telegram
webhook. Do not paste `TELEGRAM_BOT_TOKEN` into chat, a commit, or a shell argument.

## What the route does

`GET /api/telegram/digest`

| Call | Result |
| --- | --- |
| No cron secret | 401, no digest text |
| `Authorization: Bearer $CRON_SECRET` | Dry render. JSON includes the Bosnian text. No Telegram send. |
| `?mode=send` while `TELEGRAM_DIGEST_SEND` is unset | Dry render, reason `send_disabled`. No claim, no send. |
| `?mode=send` with `TELEGRAM_DIGEST_SEND=1` outside 09:40–09:59 Europe/Sarajevo | Skip, reason `outside_window`. |
| `?mode=send` with send armed, inside that window, first time that Sarajevo date | `sendMessage` to `TELEGRAM_CHAT_ID`. |
| Same Sarajevo date again | Skip, reason `duplicate_send`. |
| `?mode=test-send` plus header `X-Digest-Confirm: SEND_TEST` | One real send even outside the window. Same daily claim. |

Vercel cron is declared twice so 09:45 local survives summer and winter time:

- `45 7 * * *` UTC → 09:45 in summer (CEST)
- `45 8 * * *` UTC → 09:45 in winter (CET)

The other slot is outside the window and does not send. Both paths include
`mode=send`. Send stays off until `TELEGRAM_DIGEST_SEND=1`.

The message is compact Bosnian with short English labels, capped at 1200
characters. It always includes partial/empty/stale status, observed asking-price
cuts, and IDs first observed by us. A clean anchor older than 14 days (today:
1 October) suppresses trend-index wording and labels the counts. First observed
is not a publish date. Search absence is not a sale.

Optional `TELEGRAM_MARKET_URL` adds Market, Compare, and Following links on the
same HTTPS origin (`/market`, `/compare`, `/following`). Those preview URLs are
still behind Vercel login. Friends in the Telegram group may not be able to open
them.

## Env (names only)

Set these in Vercel Production. The token is already in the Grok Bot vault as
login `t.me` (id 3166033). Copy it from there. Do not invent a new bot.

| Name | Notes |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Vault only. Never log it. |
| `TELEGRAM_CHAT_ID` | `-5332163835` |
| `BOT_TIMEZONE` | `Europe/Sarajevo` |
| `DATABASE_URL` | Existing restricted app role, Supabase transaction pooler, port 6543, TLS. Not the collector role and not a service-role key. |
| `CRON_SECRET` | 16+ random characters. Vercel sends it as `Authorization: Bearer`. `TELEGRAM_CRON_SECRET` is accepted if `CRON_SECRET` is unset. |
| `TELEGRAM_DIGEST_SEND` | Leave unset until the dry render looks right. Then set `1`. |
| `TELEGRAM_MARKET_URL` | Optional HTTPS origin or `/market` URL for the three buttons. |
| `TELEGRAM_DIGEST_LEDGER` | Optional file path for the one-send-per-day claim. On Vercel the default is process memory, so a cold start can repeat a send. Set a durable file only on a long-lived host, or accept one retry risk until a durable claim exists. Do not add a table or change grants for this scaffold. |

Webhook-only variables stay as in `docs/TELEGRAM_BOT.md` (`TELEGRAM_WEBHOOK_SECRET`,
`TELEGRAM_ALLOWED_USER_IDS`). The digest does not use them. Phase 2 questions add one
optional name, `TELEGRAM_BOT_USERNAME` (`mojagaraza_bot`); it is not required and nothing in
Production changes until Ogi approves a deploy.

## Deploy checklist

1. Read a dry render locally against a read-only connection. Export the env vars
   in the process environment (not in the repo), then request
   `GET /api/telegram/digest` with the bearer cron secret. Confirm the text says
   the latest run is partial, the clean anchor is old, and counts are labeled
   first-observed / asking-price. Confirm it does not say sold, newly posted, or
   a recommendation.
2. Put the env vars on the Vercel **Production** environment. `DATABASE_URL` is
   still only on Preview today, so production needs it explicitly. Do not put the
   token in Preview logs.
3. Deploy this commit. Confirm `/market` and `/api/overview` still return the
   closed-workspace response. Confirm `/api/telegram/digest` without the bearer
   secret returns 401 and does not include digest text.
4. Call the deployed URL once with the bearer secret and no `mode=send`. Read the
   JSON `text`. This is the dry render.
5. One explicit test send, only after that text is acceptable:

   `GET /api/telegram/digest?mode=test-send`

   Header `Authorization: Bearer $CRON_SECRET`

   Header `X-Digest-Confirm: SEND_TEST`

   Expect one group message. A second call the same Sarajevo date must return
   `duplicate_send` and must not post again.
6. Only then set `TELEGRAM_DIGEST_SEND=1` and redeploy so the two cron slots can
   send. Leave it unset if the group should stay quiet.
7. Do not run `scripts/configure_telegram.py set-webhook` in the same step unless
   the interactive bot is also being activated. That command is a separate
   production change.

## Still blocked

- Collector: partial 5 November run, clean anchor 1 October. The digest warns. It
  does not make collection healthy.
- GitHub shadow collection has not made a real source visit. DigitalOcean stays.
- No new database role and no schema change. Idempotency on Vercel is best-effort
  until a durable ledger is approved.
- Interactive webhook bot is still not registered in production.
