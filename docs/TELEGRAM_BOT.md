# Telegram database bot

## Current safe stage

The repository now contains a private-group Telegram webhook for the existing Vercel
FastAPI application. It is not live until the production secrets, production database
connection and Telegram webhook are deliberately configured.

The bot is deterministic and database-grounded. It does not call an LLM or answer
from generic model knowledge. It exposes only these bounded commands:

- `/status`: collector status, counts, errors and freshness;
- `/latest`: the most recent run with observations and up to five IDs first observed
  by Car Analyzer in that run;
- `/trends 7`: observed asking-price transitions with period and sample sizes;
- `/budget 25000 Golf`: up to five recently observed database matches;
- `/compare Volkswagen Golf | Škoda Octavia`: broad 90-day asking-price evidence; and
- `/help`: guidance and limitations.

Buttons repeat Status, Latest, 7-day trends and 30-day trends. An optional HTTPS
Market link can be included. The bot says **first observed by us**, never "newly
posted," and never interprets search absence as a sale.

## Security boundary

The webhook route is `/api/telegram/webhook`. It is the only route allowed through
the application's production fail-closed middleware. Every request must carry the
exact Telegram `X-Telegram-Bot-Api-Secret-Token` header. The handler then checks both
the configured private group ID and the sender's user ID. Unauthorized updates are
acknowledged without a response to prevent information leakage and retries.

The bot token, webhook secret, group ID, allowed user IDs and database URL stay in
Vercel Production environment variables. The token and database password must never
be pasted into chat, an issue, a commit or a shell argument. API failures are reduced
to generic classes; the token is never logged. Database queries are parameterized,
read-only by construction and use an eight-second transaction-local statement
timeout. The production UI and all non-Telegram API routes remain closed.

Required Vercel Production variables:

- `DATABASE_URL`: the existing restricted `car_analyzer_app` Supabase transaction-
  pooler URL (port 6543, TLS required);
- `TELEGRAM_BOT_TOKEN`;
- `TELEGRAM_WEBHOOK_SECRET`: 16–256 random letters/numbers/underscores/dashes;
- `TELEGRAM_CHAT_ID`: the exact private group/supergroup ID;
- `TELEGRAM_ALLOWED_USER_IDS`: comma-separated Telegram numeric IDs for Ogi and the
  friend; and
- `TELEGRAM_MARKET_URL` (optional): HTTPS link for the Open Market button.

Do not use a Supabase service-role key. The existing server-side app login already
has bounded read access to collector evidence. The bot code does not expose Following
or candidate mutations to Telegram.

## Private setup sequence

1. Add the bot to the private group and send `/start` from each person who should be
   allowed. Keep BotFather privacy mode enabled; commands, replies and button callbacks
   are sufficient.
2. Before a webhook exists, load `TELEGRAM_BOT_TOKEN` into the local process
   environment without putting it in shell history, then run:

   ```bash
   python scripts/configure_telegram.py discover-ids
   ```

   Copy the exact group and user IDs directly into the Vercel Production environment,
   not into source. Discovery fails closed if a webhook is already active.
3. Generate a random webhook secret privately and add all required variables to the
   Vercel **Production** environment. The current project has `DATABASE_URL` only in
   Preview, so production database access must be explicitly provisioned.
4. Deploy this reviewed commit to the production alias. Verify that `/market` and
   `/api/overview` still return the closed-workspace response while an invalid webhook
   secret returns 401.
5. Load the same bot token and webhook secret into a private local environment and
   configure Telegram once:

   ```bash
   python scripts/configure_telegram.py set-webhook \
     --url https://YOUR_PRODUCTION_HOST/api/telegram/webhook \
     --confirm SET_WEBHOOK
   ```

6. Run `python scripts/configure_telegram.py inspect`, then send `/help`, `/status`,
   `/latest`, `/trends 7`, a budget query and a comparison from each allowed account.
   Confirm an unapproved account receives no database response.

`set-webhook` is an external production mutation and must not be run without explicit
approval. Telegram retries non-2xx webhook responses; inspect before retrying a failed
configuration. `getUpdates` ID discovery cannot operate while a webhook is active.

## Plain-language questions (Phase 2, code only until deployed)

Ogi can ask ordinary questions in English or Bosnian. There is still **no LLM**: a
deterministic parser (`telegram_intent.py`) turns a question into one of a few fixed,
parameterized, read-only queries (status, latest, trends, find, model prices, compare).
Anything it cannot map becomes an explicit "I can't answer that" reply, never a guess.
It cannot run arbitrary SQL and cannot change data.

Examples: `Golf from 2016 under 15000 KM`, `Škoda Octavia under 150000 km mileage`,
`how much is an Octavia`, `Golf vs Octavia`, `what's new`, `kako se mijenjaju cijene
zadnjih 14 dana`, and `what car should I buy` (answered with a request for a budget,
because the database can show observed listings but cannot pick a car).

How a message reaches the bot (Telegram privacy mode stays ON):

- `/ask <question>`;
- `@mojagaraza_bot <question>`; or
- a reply to one of the bot's own answers.

Other group chatter, even from an allowed user, is ignored. Setting
`TELEGRAM_BOT_USERNAME` is optional; when set, only mentions of that exact username and
replies to that bot count. Unset, any `@...bot` mention or reply to a bot counts.

**Follow-ups.** Nothing is stored. A reply such as `from 2018`, `under 12000`, `what
about Passat` or `and 30 days?` is combined with the scope parsed from the bot answer it
replies to, and only from that message in the same update. Context therefore cannot cross
users or chats, and replying to a human message gives no context. Find answers also carry
`Budget −10%` / `Budget +10%` buttons that encode the whole query in the callback.

**Honesty rules in answers.** Matching is over listings observed in the last 90 days and
priced listings only. Results state "N of M matches". Year and mileage filters say that
listings without those fields are excluded. Empty results, a database with no
observations, data older than 48 hours, and a latest collector run that was not
`completed` are each stated. Samples under 5 priced listings carry a thin-sample
warning. Forecasts and non-KM currencies are refused. A database error returns a generic
notice with no internals.

## Private chat and the Gemini fallback

**Private chat.** An allowlisted user can message the bot directly (open the bot, press
Start) and type plain language: no `/ask`, no `@mention`. Access requires that the sender
is in `TELEGRAM_ALLOWED_USER_IDS` **and** that the chat is a private chat whose id equals
the sender's id, so another account, a group, or a spoofed chat id gets no reply. Replies go
to the chat the message came from. The Garaža group works exactly as before.

**Short memory (private chat only).** Follow-ups like `from 2018` or `under 9000` apply to
the last find/trend answer for 30 minutes. This memory is in-process only (a serverless
instance may not share it, in which case the message is simply read on its own), keyed by
chat **and** user, bounded to 64 entries, and never used in the group.

**Gemini fallback (free tier).** The built-in parser runs first and costs nothing. Gemini
is called only when that parser cannot read the message (gibberish, or a loose sentence
such as "something sensible for my family"). Gemini never answers: it returns a small JSON
request that is validated against fixed limits (price 1,000–1,000,000 KM, years 1980–2035,
mileage, 1–90 days, sanitised search text) and then answered by the same read-only database
queries. It sees only the user's message and, for a follow-up, the previous answer's scope.
No database content, token or key is sent. If the key is missing, the free quota is used up,
or the call fails, the bot falls back to its normal reply and never errors.

- Env names: `GEMINI_API_KEY` (secret, Production) and `TELEGRAM_LLM_MODEL` (default
  `gemini-3.5-flash-lite`). The key is sent in an `x-goog-api-key` header, never in a URL.
- **Privacy:** on Google's free tier, prompts and responses may be used to improve Google's
  products and reviewed by humans. Do not type personal details into the bot. Enabling
  billing on the Google project moves it to the paid tier, which excludes this.
- `/llm` (allowlisted users only) runs one tiny test request and reports whether Gemini
  works (key rejected, quota reached, model not found, ok). It never prints a secret.

## Known MVP limits

- Phrasing outside the parser's vocabulary gets the "can't answer" reply; widening it
  is a vocabulary change, not a model change. Fuel and transmission are not filters
  because collector coverage of those fields is unverified.
- In the group a follow-up must reply to a bot answer. Private chats remember the last
  answer for 30 minutes, best-effort.
- A plain model word that is not a real make or model (`joke`) is searched like any
  other and returns "No matching listings" with a hint.
- Comparison is a broad label match over a 90-day observed cohort. It does not yet
  normalize year, mileage, fuel or transmission; the response says so.
- Telegram delivery is at-least-once. A rare retry after a successful send could
  duplicate a response because no new production deduplication table was introduced.
- The morning digest scaffold is `GET /api/telegram/digest`. It dry-renders unless
  `mode=send` is armed with `TELEGRAM_DIGEST_SEND=1` inside the 09:45
  Europe/Sarajevo window, or `mode=test-send` carries `X-Digest-Confirm: SEND_TEST`.
  Deploy steps and env names are in `NEXT.md`. It is not live.

Implementation follows Telegram's webhook secret and inline keyboard contract and
Supabase's serverless guidance to use the shared transaction pooler with TLS.
