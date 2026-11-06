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
priced listings only, ranked newest model year first, then recently seen (last 14 days), then lowest mileage, five per page with a **Show more** button. Each listing shows how long ago it was last seen; sightings older than 14 days get a ⚠ because the ad may no longer be listed. Results state "N of M matches". Year and mileage filters say that
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

**Gemini planning (free tier).** The built-in parser runs first and costs nothing. Gemini
is consulted when that parser cannot read the message (gibberish, or a loose sentence
such as "something sensible for my family"). Gemini never supplies facts: it returns a small JSON
request that is validated against fixed limits (price 1,000–1,000,000 KM, years 1980–2035,
mileage, 1–90 days, sanitised search text) and then answered by the same read-only database
queries. In planning mode it sees only the user's message, recent turns and the previous answer's
scope; the conversation mode below also sends the database answer text. No token or key is sent. If the key is missing, the free quota is used up,
or the call fails, the bot falls back to its normal reply and never errors.

- Env names: `GEMINI_API_KEY` (secret, Production) and `TELEGRAM_LLM_MODEL` (default
  `gemini-3.5-flash-lite`). The key is sent in an `x-goog-api-key` header, never in a URL.
- **Privacy:** on Google's free tier, prompts and responses may be used to improve Google's
  products and reviewed by humans. Do not type personal details into the bot. Enabling
  billing on the Google project moves it to the paid tier, which excludes this.
- `/llm` (allowlisted users only) runs one tiny test request and reports whether Gemini
  works (key rejected, quota reached, model not found, ok). It never prints a secret.

## Conversation mode (Gemini writes the reply)

When `GEMINI_API_KEY` is set, plain messages (not commands or buttons) get a chat-style
reply instead of a results dump:

- **Small talk and vague questions.** A greeting, thanks, "something for my family" or a
  bare word ("golf", "bok") goes to Gemini, which decides between a search and a friendly
  reply or a question (it asks for a budget in KM first). A message with a price, year or
  mileage is read by the free parser.
- **Searches are phrased.** The database runs the same read-only query as before, then
  Gemini rewrites *that answer* in a few natural sentences in the user's language. The
  listings (with links, ages and the ⚠ markers) are attached only when the user asked to see
  cars; otherwise the reply offers to show them. Buttons, commands and **Show more** stay
  exactly as before.
- **Grounding checks.** A model-written reply is accepted only if every number in it already
  appears in the database answer, the user's message or the recent turns, and it contains no
  links, `@` mentions or code. Otherwise the bot sends the plain database answer (or the fixed
  text). It never gives general car advice, picks a car, or forecasts prices.
- **Memory.** The last 6 chat turns are kept in-process for 30 minutes in a private chat
  (same limits as the scope memory: per chat and user, bounded, never in groups). It can be
  lost when the serverless instance changes; the bot then simply answers without it.
- A "typing…" indicator is sent while the model works.

**What Gemini sees:** the user's message, up to 6 recent turns, and the database answer text
(public olx.ba listing details such as model, price, mileage, year and age, plus counts). It
never receives tokens, keys or any other data. On Google's free tier this text may be used to
improve Google's products and reviewed by humans. Each chat message costs one or two Gemini
requests against the free quota; when the quota is used up or a call fails, the bot falls back
to its previous behaviour.

## Suggestions ("I have 50k, what should I buy?")

A budget plus a "what should I buy / what is best / what sells best" question returns a
**data-based shortlist** (not a recommendation):

- **Most listed models** within the budget (ads seen in the last 90 days, at least 3 per
  model), with listing count, median price, median mileage and newest year. "Most listed"
  means the most ads in our database; it is **not** best selling, because there is no sales
  data, and the bot says so.
- **Priced well below similar listings:** fresh ads (seen in the last 14 days) whose price is
  at least 15% under the median of the same model and year (at least 4 similar ads, compared
  across every recent ad, not only those under the budget) and whose mileage is at or under
  that median. Each shows the discount, mileage, age and link.
- Caveats are always attached: a low price can mean damage or a wrong entry; check each ad.
  Without a budget the bot asks for one. Gemini may summarise what stands out but still cannot
  invent numbers, call a car "the best", or give general car advice, and the listings are always
  attached to a suggestion.

## Language, buttons and the "looking…" message

**One language per conversation.** Every message the bot writes (results, status, help, errors,
button labels and the Gemini wording) is in Bosnian or English, all from one table
(`telegram_i18n.py`; English is the reference text, Bosnian is written the way people chat). The
language is detected from each message; if a message does not say (a button press, a bare
command), the bot uses the language that person last wrote in (in-process memory, 6 hours,
bounded), then their Telegram app language (`bs/hr/sr` → Bosnian), then
`TELEGRAM_DEFAULT_LANGUAGE` (`bs` or `en`, default `en`). Replying to an answer to refine it works in
either language. Gemini is told which language to reply in.

**Buttons that fit the answer.** There is no longer the same Status/Latest row under every reply:

- search results: **Show more**, budget −10% / +10% (or 10k / 20k / 30k when no budget), **From <year>**
  (two years newer than what is shown) and **Up to <km>** (about 30% lower mileage); when a chat
  reply summarises the results without listing them, the first button shows page one instead;
- shortlist: one button per listed model; model prices: **Show listings**; comparison: one button
  per model;
- a vague/budget question: budget chips (10k / 20k / 30k / 50k KM) that run the shortlist;
  an unanswerable one: three example questions; `/start`: a welcome with the examples;
- small talk ("hvala"): no buttons. Commands (`/status`, `/help`, …) keep the general navigation row.

**Feels alive.** When Gemini is configured, a free-text message gets an immediate "🔎 Looking…"
message that is **replaced by the answer** (edited in place; if editing fails the answer is sent as a new
message and the placeholder removed). Nothing is shown for commands, buttons, or a thank-you that gets no reply.

## Following models from chat

`prati Golf` / `follow Golf` (or `/follow Golf`) follows a model; `šta pratim` / `what am I following`
(or `/following`) lists what you follow and what is new on it.

- **Same list as the web Following page.** It uses the existing `dealer_model_searches` table, which the
  bot's database role is already allowed to insert into and delete from (that is how the web workspace
  follows a model). The bot can write nothing else. A model is identified exactly as the web does
  (`dealer_api.LISTING_SOURCE` → `market_make` / `market_model`), is saved with the web's own
  evidence as its baseline and the web's fingerprint, so following from chat or from the web is
  the same row (never a duplicate), and the stored make/model are the database's spelling.
- **Asks first.** The bot finds real models matching the words (up to three), and only a tap on a
  confirm button saves anything. The button's text is never trusted: the make and model are checked
  against real listings before saving. Unfollowing is also a button.
- **What the list shows:** listings seen in the last 90 days, the median asking price, and how many
  were **first seen by us after you started following** (not necessarily newly posted). It is
  counted from live data, not from the web workspace's last-clean-run snapshot.
- **Shared.** There is one list for the whole workspace, so everyone allowed to use the bot sees and
  edits the same follows. A followed model includes all years; the list stores the model, not a budget.
- Not available yet (needs a database permission change, see
  `docs/proposed-migrations/bot_state_and_candidates.sql`): saving a specific car to the shortlist and
  durable chat memory.

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
