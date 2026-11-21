# Bot stress suite: first run against main (389ef71), 21 Nov 2026

Suite: `tests/stress/bot_cases.yaml` (100 cases, 17 of them multi-turn, 120 messages) run by
`python -m tests.stress.runner`. Offline: parser + follow-up memory + answer routing through the real
`process_update`, no Gemini, no Telegram, no database (see `tests/stress/README.md`).

**Result on main: 56 cases pass, 44 fail.** The expectations are product intent, not a snapshot of main.

## Pass / fail by category

| Category | Pass | Fail | Total |
|---|---:|---:|---:|
| original_chat (the real failing chat) | 1 | 5 | 6 |
| price_vs_mileage (km/KM/k/hiljada/soma/kn/EUR) | 8 | 6 | 14 |
| budget_corrections | 5 | 4 | 9 |
| followups | 6 | 1 | 7 |
| mixed_intents | 0 | 6 | 6 |
| vague_advice | 6 | 3 | 9 |
| years_age | 5 | 1 | 6 |
| new_cars | 2 | 1 | 3 |
| fuel_gearbox_body | 5 | 1 | 6 |
| cities | 0 | 4 | 4 |
| typos_slang | 4 | 3 | 7 |
| aliases | 4 | 3 | 7 |
| out_of_scope (incl. greetings) | 6 | 1 | 7 |
| group_chat | 3 | 1 | 4 |
| result_format | 1 | 4 | 5 |
| **Total** | **56** | **44** | **100** |

48 messages fail inside those 44 cases (4 of them are the real-chat messages repeated in the full-conversation
case): **28 should be fixed by the 8 planned fixes, 20 are new issues.** No case crashes.

## Worst failures (actual vs expected)

Silent wrong answers first, because the user cannot tell the bot misread them.

1. **`Auto do 30 000km da nije starije od 10 godina`** (real chat). Actual: `find`, mileage ≤ 30.000 km,
   year ≥ 2016, lists cars. Expected: `clarify` / `km_unit` ("price or mileage?"). Fix 1.
2. **`Bilo koji model koji se brzo prodaje do 30000kn`** (real chat). Actual: `liquidity` with
   `search="bilo kn"`, "not enough data", 0 buttons (dead end). Expected: price bound 30.000, no model words,
   at least one next step. `Golf 6 do 30000kn` gives `search="golf kn"`, `auto do 30000 kn` gives
   `search="kn"`. Fixes 1 + 6.
3. **`Budžet je 30k a prva dva su izvan budžeta`** (real chat). Actual: `unsupported`. Expected: a search with
   budget 30.000. After a search, `ovo je izvan budžeta, imam 12k` becomes `search="izvan budzeta"` and
   `sve preko budžeta, do 7000` becomes `search="budzeta"`. Fix 2.
4. **`Ok imam 30k budžet. Koje auto da kupim koje mogu brzo prodati. Koji su trendovi`** (real chat).
   Actual: `trends` only (the last sentence wins), budget dropped, no mention of what was skipped. Expected:
   `liquidity`/`suggest` with budget 30.000 plus a line about the skipped trends question. Fix 4 (it needs
   "first clear part", while the parser checks trends before everything else). The other 5 mixed cases lack
   the skipped-part line too. Fix 4.
5. **Budget not remembered**: `imam 20k budžet` then `koji su modeli?` gives budget `None`. In the real chat
   `Da ali koji auto, koji modeli` gets a generic "give me a budget". Fix 3.
6. **`auto do 10000 KM` / `auto do 6000 KM`**: listing query ranks by recency. Expected cheapest first. A
   search with a model stays recency-first (guard case passes). Fix 5.
7. **Sold-fast dead end**: `koji se auto brzo prodaje do 15000 KM` with thin data ends "Nema dovoljno podataka"
   and 0 buttons (also with no budget). Fix 6.
8. **`passat do 8000 evra`** (NEW, silent and harmful): `evra` is not recognised as a currency, so the search
   runs as `search="passat evra"`, budget 8.000 **KM**. `EUR`, `eura`, `€` are caught correctly (clarify
   currency).
9. **`Golf do 15 soma`** (NEW): the slang "soma" (= 1.000 KM) is unknown: `search="golf 15 soma"`, no budget.
   `nesto jeftino za grad do 5 soma` falls to a generic clarify.
10. **City words leak into the model** (NEW, 4/4 cases): `golf do 15000 KM u Sarajevu` gives
    `search="golf sarajevu"`; `octavia iz Banje Luke` gives `search="octavia iz banje luke"`. There is no city
    filter, so downstream model repair may drop the word, but the user is never told the city was ignored.
11. **Greetings / thanks leak into the model** (NEW): `Zdravo, treba mi golf 7 do 15000 KM` gives
    `search="zdravo golf"`; `hvala, a imaš li passat dizel` gives `search="hvala imaš passat"`;
    `hvala puno` is a search for "hvala puno". Production may route the last two through Gemini (no number in
    the text), the first has a number so it never does.
12. **Result format** (fix 7): rows show no fuel / gearbox / city; save buttons read `⭐ 2016 · 17.500 KM` with no
    make or model; with several filters the notes under the results total 226 characters (limit set at 120).
13. **`/help` in a group** still contains "U grupi me označi ili odgovori…" (fix 8). Answers themselves carry
    no such reminder.

Other new issues (all minor): `novi golf` and `nekorišten` are not "new car" (`search="novi golf"`,
`new_ok` false, while `nov auto` and `0 km` work); `octavia novija od 2018` leaves `novija` in the search;
`polo maksimalno 9000 KM` leaves `maksimalno`; `bembara` is not BMW, `reno klio` is not Clio (`search="reno klio"`),
`folksvagen pasat` keeps `folksvagen`, `koja kola da uzmem do 12k` searches "kola uzmem"; `neki SUV do 25000 KM`
searches the model "suv" (no body-type data); `golf ili pasat` is a `find` for "golf passat", not a compare
(the one-s typo breaks compare detection); `a novije?` after a search is `unsupported`.

## Planned fixes versus new issues

**Covered by the 8 planned fixes (28 failing messages, 24 cases)**

| Fix | Failing messages | What still fails on main |
|---|---:|---|
| 1 km vs KM, kn | 8 | orig-1, `auto do 20000 km`, `do 30000km novije od 2018`, three `kn` cases, the `bilo kn` case |
| 2 budget corrections | 4 | `Budžet je 30k…`, `imam X`, `izvan budžeta`, `preko budžeta` (all lose the model or the number) |
| 3 remember budget | 2 | `koji su modeli?` after `imam 20k`, `Da ali koji auto…` in the full chat |
| 4 mixed questions | 6 | trends-only answer, no "what I skipped" line in 5 other mixed cases |
| 5 cheapest first, no model | 2 | `auto do 10000 KM`, `auto do 6000 KM` |
| 6 no dead end | 4 | `bilo kn` case, two liquidity cases, empty-data liquidity |
| 7 tidy results | 3 | row detail, save-button labels, footer length |
| 8 drop "tag me" | 1 | `/help` in a group |

Two messages count under two fixes, so the rows add up to 30. Two cases the fixes only partly help:
the kn fix must also stop `kn` leaking into the search text (item 2 above), and fix 2 must read `imam 12k`
inside a sentence that also says `izvan budžeta`.

**New, not covered by any planned fix (20 failing messages):** `evra`, `soma`, city words, greeting and thanks words
in the search, `novi` / `nekorišten`, `novija od`, `maksimalno`, `bembara`, `reno klio`, `folksvagen`, `kola`,
body types (SUV), `golf ili pasat` compare, `a novije?`, `hvala puno`, `nesto jeftino za grad do 5 soma`.

## Caveats

- Parser-only: production sends numberless free text (`hvala puno`, `novi golf`, `bembara`) to Gemini first, which
  may fix some of the 20 new ones. Messages with a number are never sent to Gemini.
- The fake database holds three listings, so this checks routing, filters, ordering and formatting, not
  whether real queries return the right cars. City words may be repaired downstream by `resolve_search`.
- `sort` is read from the listing query's ORDER BY; fix branches that sort another way may need the heuristic
  adjusted. `covers` tags are my judgment of what each planned fix targets.
- Some reply-text checks (the "skipped" line in mixed cases) match a list of plausible Bosnian wordings and
  may need the exact wording once fix 4 lands.
