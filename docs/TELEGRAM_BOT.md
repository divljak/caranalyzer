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
   allowed. With BotFather privacy mode enabled, commands, mentions, replies and button
   callbacks work; the group window and untagged answers in a small all-allowlisted group
   need it disabled (see "Small private groups" below).
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

How a message reaches the bot with Telegram privacy mode ON (see "Small private groups" for OFF):

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

**Ranges.** `od 50 - 60k`, `50-60k`, `između 50 i 60 hiljada` and `50.000 do 60.000 KM` set both a
minimum and a maximum price; `godište od 2022 - 2024`, `2022-2024` and `2022 do 2024` set both a
minimum and a maximum year. Both ends go into the SQL (listings, shortlist counts and medians,
liquidity), the answer title states them (`50.000–60.000 KM · 2022–2024`), and the buttons carry
them (`f:50000-60000:2022-2024:...`; buttons sent before ranges existed still work).

**Corrections.** "Mislim da sam stavio …", "ne, nego …", "rekao sam …", "I meant …" (or any reply to
the bot's answer) are merged into the previous question instead of starting over: the corrected
bounds replace the old ones, everything else (intent, model, other bounds) is kept, and the
question is run again. The description of what the bot showed ("a ti mi pokazuješ škodu od 33k")
is ignored. After a price question, "50.000km do 60.000km" in a correction means KM unless the
message talks about mileage. In the group, a correction may reuse the same person's previous
question from the in-process memory (30 minutes, keyed by chat and user); with no previous question
known the bot asks for the search again. It never answers that it cannot change searches, and
Gemini readings or phrasings that drop or change a stated range end are discarded in favour of
the parser and the plain database answer.

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

**Short memory.** In a private chat, follow-ups like `from 2018` or `under 9000` apply to
the last find/trend/shortlist answer for 30 minutes. This memory is in-process only (a serverless
instance may not share it, in which case the message is simply read on its own), keyed by
chat **and** user, and bounded to 64 entries. In the group it is used only for a person's
correction of their own previous question.

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
- Not on the list: durable chat memory (it needs a new table; see the optional, unused
  `docs/proposed-migrations/bot_chat_state.sql`).

## Saving cars to the shortlist

Under search results there is a **⭐ <year> · <price>** button for each car shown (two to a row). Tapping
one adds that car to the web workspace's shortlist (`dealer_candidates`); `/shortlist`, "my shortlist",
"moja lista" or "šta sam sačuvao" lists the saved cars with their status, price, price change since
you saved it, and how long ago each was last seen (⚠ when old).

- **Never overwrites the web's work.** The bot adds a car only if it is not already there
  (`ON CONFLICT DO NOTHING`), so a status or note you set on the web is never touched, and it removes only a
  car it added itself (still `shortlisted` with an empty note); otherwise it says the car has a status or note
  on the web and leaves it.
- **Needs one database change, which is not applied.** The bot's role can only read `dealer_candidates`
  today. Until the owner runs `docs/proposed-migrations/shortlist_write_access.sql` (least privilege:
  `INSERT` and `DELETE` only, no `UPDATE`, with policies and a rollback) the bot answers "saving cars isn't
  switched on in the database yet" instead of failing. The file is outside `supabase/migrations`, so nothing
  applies it automatically. A test applies it to a role shaped like the bot's, with row-level security on,
  and checks that saving then works, an update is still refused, and the rollback restores the refusal.
- Listing ids in buttons are validated (letters, digits, `_`, `-`, ≤ 50) and checked against real listings.
  The list is shared with everyone allowed to use the bot, like Following.

## Stray words never become a "model"

The words left over after the price, year and mileage are taken out used to be searched as the car's
make/model, so `imam budzet. je 50000 KM` searched for a model called "budzet. je" and found nothing.
Three layers now prevent that:

1. words are stripped of surrounding punctuation, and everyday Bosnian and English filler words
   (`je`, `li`, `jel`, `možeš`, `dovoljno`, `ćao`, `please`, …) are never part of a model name;
   Gemini's `search` text is cleaned the same way; a long sentence with no price/year/mileage is "can't
   answer", not a search;
2. if a search word still matches **no real make or model** and a budget, year or mileage was given, it is
   ignored and the answer says so ("I didn't recognise “…” as a car make or model, so I searched all
   models"); the follow-up buttons then carry the filters that were really used, not the stray word;
3. a real model that simply has no match (`Golf` under 100 KM) is never ignored.

**Buttons edit in place.** Tapping a refinement (budget −/+10%, newer, lower mileage, "show listings")
edits the message it is under instead of adding another one; **Show more** still sends a new message so page
one is not lost. If the edit fails the answer is sent as a new message and nothing is deleted. The
"show listings" button only appears when there are listings to show.

## Which groups the bot answers in (and when a group moves)

The bot answers an allowlisted person in their own private chat and in the group(s) named by
`TELEGRAM_CHAT_ID`. That setting can list **several** groups, separated by commas (`-5332163835,-1001234567890`);
the first is the primary one (the digest is sent there). Everything else is ignored.

**A group can silently get a new id.** Telegram changes a group's id when a basic group is upgraded to a
supergroup (history visibility, admin settings, topics, making it public…). The bot's setting still names
the old id, so Telegram keeps delivering messages and the webhook answers `200`, but the bot ignores them.

To make that visible, when an **allowlisted person** addresses the bot in a group that is not configured
(a command, an @mention, or a reply to the bot) the bot answers once with that group's chat id and what to do:
add the id to `TELEGRAM_CHAT_ID` in Vercel (comma-separated) and redeploy. It also does this when it sees the
"group upgraded" service message from one of its groups. It stays completely silent for strangers, private
chats, buttons and ordinary group chatter, and the reply contains nothing but the chat id. A warning line
(chat type and id, never the message) is written to the runtime logs. To diagnose without that hint, look at
`vercel logs requests` for the webhook: `200`s with no reply mean the bot received the message and ignored it.

## Talking in a group: tag once, then just write

In a **private chat** the bot reads everything. In a **group** it answers when it is tagged (`@mojagaraza_bot …`),
replied to, or used with a command. After it has answered someone it keeps listening to **that person** for
**5 minutes** (every answer restarts the timer), so a conversation doesn't need a tag on every line:
`@mojagaraza_bot Golf do 15000 KM` … `a do 9000 KM?` … `Octavia`. It is per person and per group, in-process
and bounded like the other memory (it can be lost when the server instance changes, then tag it again),
and never lets a stranger in. `TELEGRAM_GROUP_WINDOW_SECONDS` changes it (0 turns it off, max 3600).

Telegram only *delivers* un-tagged group messages to a bot that is a group **admin** or has privacy mode off
in BotFather; otherwise the bot never sees them and tagging is the only way (see the BotFather step below).

Gemini is told whether it is in a private chat or a group and may not claim that people can write without tagging
it, which it had done ("no need to tag me") while ignoring the next message. The help text explains the rule.

**Why didn't it answer?** When the bot ignores a message that addressed it, one line is written to the runtime logs:
`telegram update ignored: chat_type=… chat_configured=… user_allowed=…` (no ids, no text). If the sender is
Telegram's anonymous-admin account (an admin posting with "Remain anonymous" on), the bot cannot know who
it is and says so, in a configured group only. A group the bot is not configured for is covered above.

### Small private groups: car questions without a tag

In a group where **everyone except the bot is on `TELEGRAM_ALLOWED_USER_IDS`** and that has at most
10 members, the bot also answers plain messages that are clearly car questions (a price, year, mileage,
"za porodicu", "najlakše prodati", a comparison, …) without being tagged. Ordinary chatter ("ajmo na kafu",
"haha", "ok") is still ignored. The bot checks the group with Telegram's read-only `getChatMemberCount`
and `getChatMember` (one call per allowlisted person), caches the result for an hour, and treats any other
member, a larger group, or an API error as "tag me".

**This needs Telegram to deliver un-tagged messages.** Check with `getMe`: `can_read_all_group_messages`
must be `true`. If it is `false`, Ogi does this once (nothing in code or env changes):

1. BotFather → `/setprivacy` → choose `@mojagaraza_bot` → **Disable**.
2. Remove the bot from the group and add it again (Telegram applies the privacy change on re-join).
   Making the bot a group admin has the same effect without changing privacy mode.

### A vague tag answers what you just asked

When someone tags the bot with something vague (`@mojagaraza_bot hey`, `probaj ovo`, `možeš li ovo`), the bot
answers that person's most recent unanswered car question in the same group from the last
`TELEGRAM_GROUP_WINDOW_SECONDS` (default 5 minutes). Up to three such messages per person are kept
in memory only (never stored), per group and person; a used question is forgotten.

### Language

The reply follows the language of the message (Bosnian, Croatian and Serbian all get Bosnian copy, help and
fallbacks included). A message with no clear language ("hey", "ok", a number) uses what that person last
wrote in, then what was last written in that chat, then their Telegram app language, then
`TELEGRAM_DEFAULT_LANGUAGE` (English if unset).

## Search answers (formatting, freshness, plausibility, family)

Search answers are Telegram HTML (`parse_mode=HTML`): bold title, the car name is the link (no raw URLs),
bold price, link previews off. If Telegram rejects the formatting or the answer would exceed the length
limit, the same answer is sent as plain text, so formatting can never lose a reply.

- **Freshness first.** By default only listings seen in the last **14 days** are shown. How many older
  matches (seen 14–90 days ago) were left out is stated, with a **🕰 Uključi starije** button; writing
  "i starije" / "include older" (or replying to an answer with it) does the same. Order: most recently seen
  day first, then newest model year, then lowest mileage.
- **Plausibility.** Conservative checks flag a listing with **⚠ Neobični podaci** and move it to the end of the
  results; nothing is hidden and stored data is never changed: a car at most 5 model years old with more than
  80.000 km per year of age; a model year more than 8 years above that model's usual years (90th percentile of
  at least 8 dated listings, e.g. a "2022" Mercedes E210); or a price under 35% of the median for the same model
  and year (at least 5 listings).
- **"Za porodicu" / family / karavan / monovolumen.** The database has no body-type column, so family searches are
  limited to a short curated list of roomier models (estates, MPVs, SUVs; `FAMILY_MODELS` in `telegram_bot.py`) and
  the answer says so. It narrows the search; it never calls a car "the best". The suggestion shortlist honours it too.
- **Gemini phrasing.** Gemini sees the answer as plain text (no links). Before, any reply that named the site
  ("na olx.ba …") failed validation and the plain list was sent; the persona prompt itself said "olx.ba". A bare
  site name is now rewritten to "OLX"; real links, other domains, markup and numbers not in the facts are still
  rejected, and the log line now says why (`gemini phrasing rejected by validation: number not in the facts`,
  never the text). Messages with a price, year or mileage are still read by the free parser; only the reply is
  phrased by Gemini.

## "Najlakše prodati": what leaves our search fastest

"Auto do 20000km, staro do 10 godina koje najlakse mogu prodati", "šta se najbrže prodaje", "likvidno",
"what sells fastest" ask which models **disappear from our search** fastest. For listings first seen in the last
180 days that have not been seen for 7+ days, the bot shows the median days between our first and last sighting
per model, with how many disappeared out of how many were seen, and only ranks models with at least 5 that
disappeared. It says plainly that disappearing is not a confirmed sale (deleted, expired or outside the pages we
check) and that days count from our first sighting, not the posting date.

Units: a number followed by **lowercase `km`** is mileage (`do 20000km`), **`KM`**, "maraka" or "BAM" is a price.
A mixed-case `Km` gets a one-tap question (💰 cijena / 🛣 kilometraža). "staro do 10 godina", "ne starije od 8
godina", "up to 10 years old" become "from (current year − N)".

## Morning digest layout

The digest is Telegram HTML: a bold header with the weekday, one status line (**✅ Sve radi** when the latest run
completed with no errors, **⚠️** only when something is wrong), price drops as linked car names with the new price
in bold, the old one struck through and the % change, a **🆕 Najzanimljivije novo** section only when a listing first
seen in the latest run is priced at least 15% under the median of 4+ similar ads (same model and year) with mileage
at or under theirs, one short italic honesty line, and the collector details in an expandable quote at the bottom.
Buttons: **📉 Svi padovi** (the 10 largest drops of the last 7 days), **🆕 Novi oglasi**, **🔍 Pretraga**, plus the web
links when `TELEGRAM_MARKET_URL` is set.

## Interaction log (optional, proposed table)

`telegram_log.py` records one row per message or button tap in `bot_interactions`
(`docs/proposed-migrations/bot_interactions.sql`, **not applied**). Each row holds:

- time and chat type (private/group);
- a salted hash of the user id: HMAC-SHA256 keyed by `TELEGRAM_LOG_SALT`, or by the webhook secret when that is unset. No names, usernames or chat ids are stored;
- the message, at most 400 chars, with e-mail addresses, phone numbers and @handles redacted;
- the parsed intent and the path that read it (parser, Gemini, button, command, or a pending question used after a vague tag);
- the filters, how many results there were and how many were shown;
- whether Gemini phrased the reply, or why it fell back (e.g. `rejected: number not in the facts`);
- the button tapped, latency and an error class name.

The bot role may only `INSERT`. Logging never blocks or breaks an answer:

- it runs after the reply is sent, in a daemon thread that is waited on for at most 1 s, with a 1.5 s statement timeout;
- every error is swallowed; only the class name is logged;
- if the table does not exist (or inserts are not granted), logging silently switches itself off for an hour;
- `TELEGRAM_INTERACTION_LOG=off` turns it off completely.

Retention is 90 days. The migration holds the `pg_cron` delete and the manual `DELETE` for that.
`scripts/bot_interactions_weekly.sql` is a read-only weekly summary that shows:

- top intents;
- searches with no results;
- the Gemini fallback rate and reasons;
- the most common unanswered phrases;
- errors.

The migration also creates the `bot_interactions_weekly` view.

## Digest idempotency (optional, proposed table)

With `docs/proposed-migrations/telegram_digest_sends.sql` applied, the first claim for a Sarajevo date wins across
Vercel instances and deploys:

- the row is marked sent with the Telegram message id;
- a failed send releases the claim, so the next cron call can retry;
- a sent row can never be released.

Until the table exists, the digest uses the in-process or `TELEGRAM_DIGEST_LEDGER` file ledger exactly as before. The
response's `ledger` field says which one was used. Other database errors fail closed (no send) rather than risk a duplicate.

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
