# Bot stress suite

306 realistic Bosnian / Serbian / Croatian (and mixed, Cyrillic, English) questions and multi-turn conversations
for the Garaža Telegram bot, with the expected intent, filters and behaviour for each. Fully offline: no
Telegram, no Gemini, no database, no env or Supabase access (`harness.py`).

- `bot_cases.yaml`: the first 100 cases (21 Nov 2026, report `docs/qa/bot-stress-suite-2026-11-21.md`).
- `bot_cases_v2.yaml`: 206 adversarial cases the first suite missed, ids `v2-...` (report
  `docs/qa/bot-stress-suite-v2-2026-11-21.md`).

- `bot_cases_year_rule.yaml`: 9 cases for the exact-year rule (GAR-8), ids `v2-yr-...`; they use the harness dataset
  `golf_years=2014,2015,...` (one Golf per year, year bounds applied).

```bash
# a table of pass/fail per category + every failure with actual vs expected (exit 0)
python -m tests.stress.runner
python -m tests.stress.runner --original         # only the first 100
python -m tests.stress.runner --new              # only the v2 cases
python -m tests.stress.runner -k budget          # ids / categories containing "budget"
python -m tests.stress.runner --json out.json    # machine-readable
python -m tests.stress.runner --strict           # exit 1 on any failure (use once the fixes have landed)

# as tests (skipped unless BOT_STRESS=1, so normal runs are unaffected)
BOT_STRESS=1 python -m unittest tests.stress.test_bot_stress -v
BOT_STRESS=1 python -m pytest tests/stress -m bot_stress
```

Needs PyYAML (`pip install pyyaml`). Run it from the repo root on whichever branch you want to check.

## How a message is run

`harness.Session.say()` sends a Telegram update through the real `telegram_bot.process_update` with
`llm=None, phraser=None` (the deterministic parser only, as in production when Gemini is off or the parser is
sure), a recording stand-in for the Telegram client, a fake SQLAlchemy engine and a stubbed interaction log.
Multi-turn cases use the bot's own follow-up memory, exactly as a private chat does; `reply: true` makes a turn
a Telegram reply to the previous bot message. Every case starts with all of the bot's in-process memories empty
(`harness.BOT_MEMORIES`: scopes, budgets, history, languages, group windows, pending questions, open groups).

The fake engine records every SQL statement. With `dataset: rows` it knows ~60 make/model labels
(`harness.CATALOGUE`): the known-model check, the typo / dropped-word repair (`resolve_search`) and the follow
lookup run against them, and a model search returns only the canned listings whose label matches (or two made-up
listings of a matching catalogue model, or nothing). So a leaked word shows what production shows: "Nisam
prepoznao „golf iz mostara“, pa tražim „golf“", "Jesi li mislio na …?" or no match. Price / year / mileage bounds
are not applied to the canned rows. `dataset: empty` answers nothing.

Each failing message is tagged `[parser-only]` or `[Gemini first in prod]`: production asks Gemini first for
search-only text without a number (`telegram_bot._needs_interpreter`), so a parser miss on such a message may be
softened there. A message with a price, year or mileage never reaches Gemini, so its parser reading is exactly
what the user gets.

`sort` is read from the listing query's ORDER BY, skipping CASE terms whose bind-parameter guard is off for that
query (e.g. `CASE WHEN :search='' THEN price END` is inert for a search with a model).

## Case schema

`id, category, title, chat (private|group), dataset (rows|empty), impact?, turns[]`. `impact` (v2) says what a
failure means for a user: `silent` (wrong answer the user cannot tell is wrong), `dead_end`, `visible`,
`cosmetic`, or `guard` (works today, protects against regressions).

A turn is `say, reply?, untagged?, user?, covers?, expect`. In a group chat every message is tagged `@testbot`
unless it is a reply, a command, or `untagged: true`; `user: 456` sends that one message as another allowlisted
person. `covers` lists which of the 8 answer-quality fixes should make this turn pass; a failing turn with no
`covers` is a new issue. Fixes: 1 km/KM/kn ask, 2 budget corrections, 3 remember budget, 4 mixed questions,
5 sort cheapest first without a model, 6 never a dead end for "sells fast", 7 tidy results (footers, save
buttons, row detail), 8 drop the "tag me in the group" reminder.

`expect`: `intent` (str or list), `note`, `filters{}`, `search_has[]`, `search_lacks[]`, `sort`, `reply_has[]`,
`reply_has_any[]`, `reply_lacks[]`, `buttons_min`, `buttons_have[]`, `buttons_have_any[]`, `footer_chars_max`,
`handled`, `labels_has[]`, `labels_lacks[]`, `lt{}` / `gt{}` (a filter strictly below / above a value, e.g.
"a jeftinije?" → `lt: {budget: 15000}`), `days` (trends window), `reply_chars_max`, `any_of[]` (alternative
acceptable readings). Years accept `"Y-10"` (this year minus 10). Text checks fold diacritics and case. The
expectations are product intent, not a snapshot of current behaviour.
