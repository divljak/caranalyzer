# Bot stress suite

About 100 realistic Bosnian / Serbian / Croatian questions and multi-turn conversations for the Garaža
Telegram bot, with the expected intent, filters and behaviour for each. Fully offline: no Telegram, no
Gemini, no database, no env or Supabase access (`harness.py`).

```bash
# a table of pass/fail per category + every failure with actual vs expected (exit 0)
python -m tests.stress.runner
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
The fake engine records every SQL statement and answers from three canned listings (or nothing, with
`dataset: empty`). Multi-turn cases use the bot's own follow-up memory, exactly as a private chat does;
`reply: true` makes a turn a Telegram reply to the previous bot message.

Caveats: production sends search-only free text (no number) to Gemini first, so some parser-level
findings (e.g. "hvala puno" read as a model search) are softened there. `sort` is read from the ORDER BY of the
listing query. Canned rows carry fuel / gearbox / city fields for branches whose rows show them.

## Case schema (`bot_cases.yaml`)

`id, category, title, chat (private|group), dataset (rows|empty), turns[]`. A turn is
`say, reply?, covers?, expect`. `covers` lists which of the 8 answer-quality fixes should make this turn pass;
a failing turn with no `covers` is a new issue. Fixes: 1 km/KM/kn ask, 2 budget corrections, 3 remember budget,
4 mixed questions, 5 sort cheapest first without a model, 6 never a dead end for "sells fast", 7 tidy results
(footers, save buttons, row detail), 8 drop the "tag me in the group" reminder.

`expect`: `intent` (str or list), `note`, `filters{}`, `search_has[]`, `search_lacks[]`, `sort`, `reply_has[]`,
`reply_has_any[]`, `reply_lacks[]`, `buttons_min`, `buttons_have[]`, `buttons_have_any[]`, `footer_chars_max`,
`handled`, `any_of[]` (alternative acceptable readings). Years accept `"Y-10"` (this year minus 10). Text checks
fold diacritics and case. The expectations are product intent, not a snapshot of current behaviour.
