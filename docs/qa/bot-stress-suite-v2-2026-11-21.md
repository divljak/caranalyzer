# Bot stress suite v2: current main (5c54e38), 21 Nov 2026

Branch `test/bot-stress-suite-v2`, based on `origin/main` at 5c54e38 (#52 and #53 merged). The first suite (draft PR #54,
`tests/stress/*` and `docs/qa/bot-stress-suite-2026-11-21.md` at f724b6c) was brought over unchanged and extended.
Everything runs offline: the real `telegram_bot.process_update` with the parser only. Nothing goes to Telegram,
Gemini or a database (see `tests/stress/README.md`).

```bash
python -m tests.stress.runner --original   # the first 100 cases
python -m tests.stress.runner --new        # the 206 v2 cases
python -m tests.stress.runner              # all 306
```

## Summary

| Suite | Cases | Messages | Pass | Fail |
|---|---:|---:|---:|---:|
| Original 100 on 389ef71 (first run) | 100 | 120 | 56 | 44 |
| Original 100 on main 5c54e38 | 100 | 120 | **67** | **33** |
| New v2 cases on main 5c54e38 | 206 | 281 | **83** | **123** |
| **All on main** | **306** | **401** | **150** | **156** |

No case crashes. Of the 123 failing v2 cases, **65 are silent wrong answers** and **21 are dead ends** (see
"Impact" below). 86 of the 135 failing v2 messages are **parser-only**: they contain a price, year or mileage,
so production never asks Gemini about them and the user gets exactly this answer.

## Harness changes (please read before comparing numbers)

1. **Memory leak between cases (bug in the v1 harness, fixed).** #53 added `telegram_bot._BUDGETS`, and the v1
   harness cleared only 4 of the bot's 7 in-process memories. On main, `šta da kupim` in a *fresh* chat was
   answered with the previous case's 25.000 KM budget, and `Da ali koji auto, koji modeli` with 30.000 KM. Each
   case now starts with every memory empty (`harness.BOT_MEMORIES`). Rerunning the fixed harness on 389ef71 still
   gives 56/44, so the first report's numbers stand.
2. **Sort heuristic was parameter-blind (fixed).** The listing query now carries
   `CASE WHEN :search='' THEN price END`, which v1 read as "sorted by price" even for `golf do 15000 KM`. Terms
   whose bind-parameter guard is off are now skipped. Without this, `fmt-model-newest-first` showed a false
   regression.
3. **Realistic model lookup.** v1's fake DB said "known model" for any search, so a leaked city or greeting word
   never showed its effect. The fake DB now holds about 60 make/model labels. The known-model check, the
   typo/dropped-word repair (`resolve_search`) and the follow lookup (`telegram_follow.resolve_models`) run
   against them, and a model search returns only matching listings. Price and year bounds are still not applied to
   canned rows. The original 100 give the same 67/33 before and after this change.
4. **New keys:** turn `untagged` / `user` (group behaviour, second member), expect `labels_has/lacks`, `lt` / `gt`
   ("a jeftinije?" must lower the cap), `days`, `reply_chars_max`, case `impact`. Each failing message is tagged
   `[parser-only]` or `[Gemini first in prod]` (from `telegram_bot._needs_interpreter`).

**Old expectations changed:** none. The v1 YAML is byte-identical; all v2 cases are in `bot_cases_v2.yaml`.

## 1. Original 100 on current main vs the first run

| Category | 389ef71 pass/fail | main pass/fail |
|---|---:|---:|
| original_chat | 1 / 5 | 3 / 3 |
| price_vs_mileage | 8 / 6 | 12 / 2 |
| budget_corrections | 5 / 4 | 6 / 3 |
| followups | 6 / 1 | 5 / 2 |
| mixed_intents | 0 / 6 | 1 / 5 |
| vague_advice | 6 / 3 | 7 / 2 |
| years_age | 5 / 1 | 5 / 1 |
| new_cars | 2 / 1 | 2 / 1 |
| fuel_gearbox_body | 5 / 1 | 5 / 1 |
| cities | 0 / 4 | 0 / 4 |
| typos_slang | 4 / 3 | 4 / 3 |
| aliases | 4 / 3 | 4 / 3 |
| out_of_scope | 6 / 1 | 6 / 1 |
| group_chat | 3 / 1 | 3 / 1 |
| result_format | 1 / 4 | 4 / 1 |
| **Total** | **56 / 44** | **67 / 33** |

**13 cases now pass:** orig-1-km-is-price, orig-3-mixed-budget-sell-trends, unit-km-lower-no-model, unit-kn,
unit-kn-no-model, unit-km-plus-year-no-model, budget-correction-izvan, budget-correction-preko,
mixed-three-questions, vague-no-model-sorted-by-price, fmt-row-detail, fmt-save-buttons-name-the-car,
fmt-no-model-cheapest-first. In the full real-chat conversation, messages 1, 3, 4 and 5 now pass. Message 2 still
fails.

**2 cases newly fail:**
- `follow-cheaper` is a **real regression from #53**. Before, `nešto jeftinije` after `golf do 15000 KM` asked for
  a budget. Now the budget-reuse rule (`telegram_bot._understand`, which reuses the remembered budget when
  `wants_recommendation` matches a vague word such as "jeftin…") turns it into a `suggest` shortlist at the **same**
  15.000 KM, with no Golf and nothing cheaper.
- `budget-correction-no-number` (`ovo je izvan budžeta` with no number) **passed by accident on 389ef71**: the
  bot searched for a model called "izvan budžeta" and the word "budžeta" in the title satisfied the check. Main now
  reruns the identical search (golf ≤ 12.000) as a "correction" and does not ask for the new budget. Its
  expectation was left unchanged; the failure is real.

### Planned fixes: which hold

The previously failing messages tagged with each fix (28 messages, some under two fixes):

| Fix | Previously failing | Now pass | Still fail | Verdict |
|---|---:|---:|---:|---|
| 1 km vs KM, kn | 8 | 8 | 0 | **Holds.** `orig-5` now fails only on fix 6's button check |
| 2 budget corrections | 4 | 2 | 2 | **Partly.** `Budžet je 30k a prva dva su izvan budžeta` is still `unsupported`, both alone and as message 2 of the real chat, where the previous turn is the km/KM question (`clarify`, not a correctable search) |
| 3 remember budget | 2 | 1 | 1 | **Partly.** `Da ali koji auto, koji modeli` works; `koji su modeli?` after `imam 20k budžet` searches the model "su" and lists Škoda **Su**perbs |
| 4 mixed questions | 6 | 3 | 3 | **Partly.** Works only across sentences (`. ! ? ;`). `trendove ali i golf do 15000`, `Golf vs Octavia, a onda … do 20000` and `koliko košta golf 7 2016 i da li se brzo prodaje` answer one part and do not mention the rest |
| 5 cheapest first, no model | 2 | 2 | 0 | **Holds** |
| 6 no "sells fast" dead end | 4 | 1 | 3 | **Does not hold** with thin data: still "Nema dovoljno podataka", 0 buttons |
| 7 tidy results | 3 | 2 | 1 | **Mostly.** Row detail and save labels are fixed; notes under the rows are 146 characters (limit 120) |
| 8 drop "tag me" reminder | 1 | 0 | 1 | **Does not hold** in `/help` (`telegram_i18n.py` "help" still says "U grupi me označi…") |

All 20 "new issue" messages from the first report still fail (evra, soma, cities, greetings/thanks, novi,
novija od, maksimalno, bembara, reno klio, folksvagen, kola, SUV, golf ili pasat, a novije?, hvala puno, 5 soma).

## 2. New v2 cases (206 cases, 281 messages)

| Category | Pass | Fail | Total | of which silent / dead end |
|---|---:|---:|---:|---:|
| long_conversations (5–8 turns) | 2 | 11 | 13 | 10 |
| currency_formats | 14 | 8 | 22 | 6 |
| price_vs_mileage_v2 | 8 | 5 | 13 | 5 |
| cities_regions | 0 | 14 | 14 | 14 |
| greetings_thanks (incl. emoji, rambling) | 3 | 11 | 14 | 7 |
| scripts_languages (Cyrillic, mixed, English) | 6 | 6 | 12 | 6 |
| typos_no_diacritics | 5 | 5 | 10 | 2 |
| aliases_slang | 9 | 6 | 15 | 5 |
| body_types | 2 | 10 | 12 | 10 |
| comparisons | 5 | 6 | 11 | 2 |
| new_vs_used | 2 | 7 | 9 | 5 |
| resale_trends_deals | 7 | 7 | 14 | 5 |
| follow_shortlist | 3 | 8 | 11 | 5 |
| group_behaviour | 7 | 1 | 8 | 0 |
| robustness (long, nonsense, injection) | 6 | 12 | 18 | 1 |
| followup_shortcuts | 4 | 6 | 10 | 3 |
| **Total** | **83** | **123** | **206** | **86** |

**Impact of the failures** (case tag): silent 65 of 70 fail, dead end 21 of 21, visible 26 of 28, cosmetic 11 of 16.
All 71 guard cases pass. They cover number formats (15.000 / 15,000 / 15 000 / 20.000,00 / BAM / hiljadarki /
od–do ranges), €/$/EUR, km-vs-KM with explicit units, generations, deals, latest, price checks, group tagging,
reply follow-ups and SQL text.

**What holds up well:** explicit units and thousands formats; group addressing (untagged chat ignored, a 5-minute
window for the same person, no context leak to a second member, replies narrow the search); `a dizel?`,
`a od 2014?`, `pokaži i starije`; English basics; prompt injection never leaks anything (only fake test
secrets exist in the harness, and none is echoed).

## 3. Top 15 failures by real-user impact

Silent wrong answers come first, parser-only before Gemini-first.

| # | Message (case) | Actual | Expected |
|---|---|---|---|
| 1 | `octavia ispod 200k km do 12k` (v2-pm-km-first-then-price) | budget **dropped**; mileage 12.000–200.000 km; lists cars of any price | budget 12.000 KM, mileage ≤ 200.000 km |
| 2 | `golf sa manje od 100 tisuća km` (v2-pm-tisuca-km) | **budget 100.000 KM**, no mileage cap | mileage ≤ 100.000 km |
| 3 | `golf do 15000 KM a da je prešao manje od 150 hiljada` (v2-pm-presao-hiljada) | mileage cap dropped; "prešao 150" in the model, then "Nisam prepoznao…" | budget 15.000, mileage ≤ 150.000 |
| 4 | `golf 4 do 7,5k` / `do 7.5 hiljada` (v2-cur-decimal-*) | **budget 5.000 KM**, model "golf 7" | budget 7.500, Golf 4 (1997–2004) |
| 5 | `passat do 10 hiljada eura`, `octavia do 12k evra` (v2-cur-eura-word, v2-cur-evra-k) | **10.000 KM** (about half the user's real budget), "eura" in the model | ask for KM (as `EUR`/`€` already do) |
| 6 | `golf ili passat do 15000`, `golf 7 ili octavia 3 do 20000` (v2-cmp-*) | "Nisam prepoznao „golf passat“, pa tražim „golf“": **Passat silently dropped** | compare, or list both |
| 7 | `više ne prati passat` / `prestani pratiti golf` (v2-follow-*) | **"Da pratim Volkswagen Passat?"**, the opposite of the request / "Nisam našao model sličan „prestani golf“" | list follows with unfollow buttons |
| 8 | `auto u RS do 10000 KM` (v2-city-rs-no-model); `koji su modeli?` after a budget (budget-remembered-across-messages) | `LIKE '%rs%'` shows **only Opel Corsas**; `'%su%'` shows **only Škoda Superbs** | budget search with no model |
| 9 | `ipak može do 16000` mid-conversation (v2-conv-budget-change-midway) | model becomes **"ipak"**: "no match", and Passat + diesel are lost for the rest of the chat | Passat diesel ≤ 16.000 |
| 10 | `a u Sarajevu?` mid-conversation (v2-conv-city-mid-conversation) | model **replaced by "sarajevu"**, kept on later turns (`a automatik?` still searches "sarajevu") | keep Octavia (say city is not a filter) |
| 11 | `a jeftinije?` / `nešto jeftinije` after `golf do 15000 KM` (v2-fu-jeftinije, follow-cheaper) | `suggest` shortlist at the **same** 15.000 KM, Golf/fuel/year dropped (regression from #53) | lower the cap, keep Golf, or ask |
| 12 | Budget not carried: `imam 20k` → `a šta se brzo prodaje` / `ok a dizel` (v2-conv-advice-flow) | liquidity and search **without** the 20.000 KM | keep budget 20.000 |
| 13 | `Budžet je 30k a prva dva su izvan budžeta` (real chat message 2) | `unsupported` ("To ne mogu povezati…"), alone and in the real chat order | search with budget 30.000 |
| 14 | `jesu li cijene pale ovaj mjesec`, `trend zadnja 3 mjeseca`, `koliko je sati` (v2-res-*, v2-rob-time-question) | price stats for a model called **"pale"** / **"sati"**; 3 months → **7 days** | trends 30 / 90 days; small talk |
| 15 | **Dead ends** on common phrasings: `polovni auto do 10000`, `polovno auto za porodicu do 15 hiljada`, `selam aleikum, treba mi auto do 10 hiljada`, `pezo 206`, `dzip`/`terenac`/`SUV`/`limuzina do …`, Cyrillic `Голф до 15000 КМ` | "Nisam prepoznao „polovni“ … **Jesi li mislio na Volkswagen Polo?**" / "Seat Leon?" / no match | budget search with no model (and a note that body type / used is not a filter) |

Close behind: city words in every position (`iz Mostara`, `u Banjaluci`, `Banja Luka`, `kod Sarajeva`,
`Istočno Sarajevo`; 14 of 14 fail). Production repairs them to the model with "Nisam prepoznao „golf iz mostara“,
pa tražim „golf“", so results are **not** filtered by city and the user is not told that location is unsupported.
`Doboj` is "typo-repaired" to **Doblo**. `prati golf 7` / `zaprati mi passat do 15000` show "you follow nothing
yet" instead of offering to follow. A 450-character voice-style message is cut at 400 characters before the
request (→ `unsupported`). `…pa sam mislio golf ili tako nešto do 15 hiljada` is read as a *correction*
("mislio sam"). Thanks (`hvala puno`, `ok hvala`, `super hvala ti`, `e super, fala`) become Fiat **Punto** /
Škoda **Superb** searches when Gemini is off. `šta je bolje, dizel ili benzin` compares two "models" called
"dizel" and "benzin".

## 4. Root causes, code locations, fix size

| # | Root cause | Where | Failures (approx.) | Size |
|---|---|---|---:|---|
| R1 | **The search text is "whatever is left".** Any word that is not a stopword becomes part of the model: cities, regions, greetings, thanks, filler (`ipak`, `hitno`, `dajte`, `zanima`, `pozz`, `imal`), used/new words (`polovni`, `rabljeni`, `nov`, `iz salona`, `nekorišten`), body types. Downstream `resolve_search` then drops or "corrects" words, or ends in "did you mean" when no model word is left | `telegram_intent._search_tokens`, `_STOPWORDS`, `_model_search`; `telegram_bot.resolve_search`, `find_page` (unknown-model branch) | ~45 | **Medium.** City/region gazetteer by stem (sarajev*, mostar*, banj* luk*, tuzl*, zenic*, bihac*, doboj*, RS/FBiH, hercegovin*), greeting/thanks/filler and used/new words as stopwords, body-type words recognised. If nothing but unknown words remains and a budget is set, run the no-model search instead of "did you mean" |
| R2 | **Substring LIKE on very short searches**: `rs` → Corsa, `su` → Superb | `telegram_bot._like_pattern`, `_is_known_model`, `_find_rows` | 2+ | **Small.** Word-boundary match (or reject) for searches of ≤ 3 letters that are not catalogue names |
| R3 | **Typo repair hits place names**: Doboj → Doblo | `telegram_intent.repair_typo` | 1 | **Small** (exclude the gazetteer from R1) |
| R4 | **Amount and currency gaps**: decimals (`7,5k`, `7.5 hiljada`) split into a generation and "5k"; `soma` not a thousands word; `eura`/`evra` not a currency; `tisuća kuna` misses the kn note; `999999999` leaks into the model | `telegram_intent._AMOUNT`, `_THOUSANDS`, `_CURRENCY`, `_KN_AMOUNT` | 9 | **Small** |
| R5 | **Mileage cues and ranges**: `prešao`/`pređe` are not mileage cues; `100 tisuća km` reads as a price; `ispod 200k km do 12k` becomes a 12.000–200.000 km range; `golf do 150 000` (no unit) becomes a 150.000 KM budget | `telegram_intent._MILEAGE_CUES`, `_KM_NOUN`, `_price_range`, `_extract_filters` | 6 | **Medium** (range pairing needs care; the regex is shared by both units) |
| R6 | **Follow / unfollow**: `_FOLLOW` matches "prati" inside `prestani pratiti` / `ne prati` (no negation); any numeric filter (`golf 7` → years, `do 15000`) turns follow into the follow list; `javi mi kad…`, `obavijesti me…` are not follow; `sačuvane`, `sačuvaj` stay in the search, so the shortlist check never fires | `telegram_intent._FOLLOW`, `_SHORTLIST`, `_parse_question` (follow branch, `if named and not filters.numeric()`) | 8 | **Small** |
| R7 | **Conversation memory**: (a) a follow-up's new search *replaces* the model (`a u Sarajevu?`, `a neki drugi model?`, `ipak …`); (b) no rule for relative follow-ups (`a jeftinije?`, `a novije?`, `može i skuplje`, `a sa manje kilometara?`); (c) the remembered budget is used only for clarify → suggest, so liquidity / fuel-only follow-ups lose it, while "jeftinije" wrongly triggers it | `telegram_intent.looks_like_followup`, `merge_filters`, `wants_recommendation`; `telegram_bot._understand` (budget reuse) | ~14 | **Medium** |
| R8 | **Compare detection**: "X ili Y" needs no digits and, without "?", two-word names on both sides, so `golf ili pasat`, `golf ili passat do 15000`, `golf 7 ili octavia 3` fail; no `protiv`, `uporedi ih/sa …`, comma lists; fuels are treated as models | `telegram_intent._compare_labels`, `_COMPARE_SPLIT` | 7 | **Small–medium** |
| R9 | **Mixed questions only across sentences**, and a budget correction needs a correctable previous search (fails standalone and after a km/KM question) | `telegram_intent._mixed_question` (`_SENTENCE_BREAK`), `_parse_question` (`_budget_correction` guard), `_correct_budget` | 5 | **Medium** |
| R10 | **Acknowledgements are single words only** (`ok hvala`, `hvala puno`, `hvala ti`, `ok thanks`, `👍`) | `telegram_intent._ACK` | 9 | **Small** |
| R11 | **Off-topic text becomes a search or stats**: `koliko je sati` → model_stats (`koliko` ∈ `_STATS`); insults, football, "phone number", "scam?", injection, keyboard mash → find. Mostly Gemini-first in production, but `koliko je sati` is parser-only | `telegram_intent._STATS`, `_parse_question` tail; `telegram_bot._needs_interpreter` (model_stats not routed) | 9 | **Small–medium** |
| R12 | **Cyrillic is not folded**: `до`, `КМ`, `дизел`, `колико кошта` are unknown | `telegram_intent.fold` | 4 | **Small** (transliterate Cyrillic → Latin in `fold`) |
| R13 | **Trends window**: `ovaj mjesec`, `zadnja 3 mjeseca` → 7 days; `cijene pale` without "trend" not recognised | `telegram_intent._days_from`, `_TRENDS` | 3 | **Small** |
| R14 | **400-character cut** drops the request at the end of a long voice-style message; `mislio sam` (I was thinking) read as a correction | `telegram_intent.MAX_QUESTION`, `_CORRECTION` | 2 | **Small** |
| R15 | **Vocabulary gaps**: estates `combi`/`avant`/`varijant` not family; generation aliases `vii`, `mk7`, `sedmica`; slang `trojka`, `merdžo`, `pezo`; `novi/nov X`, `iz salona`, `nekorišten` not new; `najjeftiniji golf` not cheapest-first; `pređe` mileage stats; `isplati li se` as a price check | `telegram_intent._FAMILY`, `GENERATIONS`, `MODEL_ALIASES`, `_NEW_CARS`, `_KM_NOUN`, `_PRICE_CHECK`; `telegram_bot._find_rows` ORDER BY | ~18 | **Small** each |
| R16 | **Leftovers from the planned fixes**: liquidity with thin data has no buttons (fix 6); `/help` keeps the group paragraph (fix 8); notes under results 146 > 120 characters (fix 7) | `telegram_bot.liquidity_message`; `telegram_i18n.py` "help"; `telegram_bot.find_page` notes | 5 | **Small** |

Suggested order: R1 + R2 + R3 (most silent/dead-end failures, one gazetteer and stopword pass), then R4 + R5
(silent number misreads, all parser-only), then R6 and R7 (wrong or opposite actions in conversations), then the
small vocabulary items.

## Caveats

- **Parser-only**: production asks Gemini first about numberless search-only text, so 49 of the 135 failing v2
  messages (`[Gemini first in prod]` in the runner output) may be answered better there. The other 86 contain a
  price, year or mileage and never reach Gemini. Gemini's answer is still checked against the parser's bounds
  (`_grounded`), so it cannot fix R4/R5.
- The fake catalogue holds about 60 labels in "Make Model" form. Real labels may differ (e.g. "Golf 7" as the
  model), which can change "did you mean" wording but not the parser findings. Price/year bounds are not applied
  to canned rows.
- `audi a4 do 25 000 km` expecting "ask km or KM" goes beyond the merged fix 1 (which asks only without a model).
  It is a product judgement call based on the real chat, where lowercase "km" meant KM.
- Reply-text checks fold diacritics and case. Wording changes may need the lists of acceptable words updated.
