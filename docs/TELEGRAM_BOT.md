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

## Known MVP limits

- This is structured conversation, not unlimited natural-language advice. Simple
  phrases such as “status,” “latest,” and “trends” work; buying queries use explicit
  commands so the evidence scope is unambiguous.
- Comparison is a broad label match over a 90-day observed cohort. It does not yet
  normalize year, mileage, fuel or transmission; the response says so.
- Telegram delivery is at-least-once. A rare retry after a successful send could
  duplicate a response because no new production deduplication table was introduced.
- The daily morning digest is the next thin layer: call the same status/trend/latest
  renderers after a successful scheduled collector run. Do not schedule it until the
  single-scheduler collector cutover is complete and Telegram interaction is verified.

Implementation follows Telegram's webhook secret and inline keyboard contract and
Supabase's serverless guidance to use the shared transaction pooler with TLS.
