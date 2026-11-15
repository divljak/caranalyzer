# Garaža bot: use-case QA sweep (15 Nov 2026)

**Code under test:** `origin/main` @ `4c2f3f7` (PR #37 + #38 merged). Not the fix branch.
**Result:** 37 turns in 31 conversations. **12 PASS · 4 WEAK · 21 FAIL.**
Of the 21 FAILs, about 7 should be fixed by `fix/bot-ranges-followups` (`79f8718`). The other 14 need separate work.

## How it was run (read-only)

- I called `telegram_bot.process_update`, the function the webhook calls, with a fake Telegram transport. Every update came from a private chat with an allowlisted QA user id. Nothing went to Telegram.
- Database: the production Supabase, through the backup role. The secret was streamed over stdin and never written to disk. Every session ran with `default_transaction_read_only=on` and `SET TRANSACTION READ ONLY`. The pipeline's own SQL was unchanged.
- `telegram_log.record` was replaced with a no-op, so nothing was written to `bot_interactions`.
- LLM: real Gemini, `gemini-3.5-flash-lite`, using the production key. The full path ran: deterministic parse → Gemini interpret when needed → DB answer → Gemini phrasing with grounding check.
- Follow-ups were sent in the same private chat, one after another. They therefore used the bot's real in-process scope and history memory.
- Gemini reliability (an operational finding of its own):
  - **Run 1** was paced at about 1 message per second. From the 16th message on it got **HTTP 429** (quota).
  - **Run 2** was paced at 9 s. Gemini **timed out** on 12 conversations, at about 8 s per call and about **16–17 s total** when both the interpret and phrase calls failed.
  - Both times the fallback worked: the user got the deterministic answer.
  - The tables below use run 2, with those 12 conversations re-run cleanly (run 3), so every case shows the bot with Gemini working.

"Filters" below are the parsed `Intent`: `kind`, `path` (parser or gemini), and the non-empty filters.

## Results

### Price ranges

| # | Input | Parsed | Reply (shortened) | Verdict |
|---|---|---|---|---|
| P1 | auto od 50 - 60k | find · parser · budget=60000 | "…cijene se kreću od 22.900 KM do 59.499 KM", 571 oglasa. First row: **Kia Sportage 2026 · 3 km** | **FAIL**: the lower bound is dropped (a 22.9k Corsa is shown), and a new 3 km dealer car tops a used-car list. *Fix branch: yes (range)* |
| P2 | nešto između 20 i 25 hiljada KM | clarify · gemini · (none) | "Može, a koji model auto tražiš?" + budget buttons | **FAIL**: the budget is given, yet the bot asks for a model and offers 10/20/30/50k buttons. *Fix branch: yes ("između", "hiljada")* |
| P3 | dizel do 15.000 KM | find · parser · search="dizel", budget=15000 | "Za dizelaše do 15.000 KM nemam ništa… u zadnjih 14 dana" | **FAIL**: "dizel" is used as model text, so 0 results. In reality there are hundreds of diesels under 15k (`fuel_type` is filled on 99% of rows). *Fix branch: no* |
| P4 | auta preko 40k | find · parser · budget=40000 | "…pokazuju oglase do 40.000 KM, a ne preko te cijene… od 19.700 do 36.900 KM" | **FAIL**: "preko" ("over") is read as a maximum. Gemini honestly says so, but the user still gets the opposite of what they asked. *Fix branch: no ("preko/iznad/over" not handled)* |

### Year ranges

| # | Input | Parsed | Reply | Verdict |
|---|---|---|---|---|
| Y1 | Golf 2022-2024 | find · parser · search=golf, **budget=2024**, year_min=2022 | "…Golf od 2022. do **2.024 KM**, ali nema odgovarajućih oglasa" | **FAIL**: the year range's upper end becomes a price of 2.024 KM, so 0 results. The worst misparse in the sweep. *Fix branch: yes* |
| Y2 | Passat od 2018 | find · parser · passat, year_min=2018 | "…Passata od 2018… 31.900 – 42.900 KM" | **PASS** |
| Y3 | auto do 30k ne starije od 5 godina | find · parser · budget=30000, year_min=2021 | 22 oglasa, 2021–2023, 19.7k–27.9k, listed | **PASS** |

### Combined, and the correction that motivated the fix

| # | Input | Parsed | Reply | Verdict |
|---|---|---|---|---|
| C1a | Pronadji mi koje auto se najbolje prodaje od 50 - 60k, godiste od 2022 - 2024 | suggest · parser · budget=60000, year_min=2022 | Most listed: Octavia (median **33.450 KM**), 3008 (34.999), Golf (40.900), **Kia Sportage median 5.000 km, najnovije 2026**. "Deal": **Toyota Corolla 2026 · 0 km, 33% ispod medijana** | **FAIL**: the 50k floor and the 2024 ceiling are both ignored, so medians fall far below the asked range (this is the "Škoda for 33k" complaint). New 0 km dealer cars skew medians and are offered as deals. The "no sales data" caveat is good. *Fix branch: range yes; outliers no* |
| C1b | Mislim da sam stavio gore od 50.000km do 60.000km a ti mi pokazujes skodu od 33k | unsupported · gemini · (none) | "Hej, izvini, ja stvarno ne pratim te detalje oko kilometraže ili cijena napamet… Hoćemo li probati tražiti ponovo?" | **FAIL**: context is lost and the bot deflects, so the user has to start over. *Fix branch: yes (correction merge)* |

### Mileage vs price

| # | Input | Parsed | Reply | Verdict |
|---|---|---|---|---|
| M1 | Octavia do 150.000 km | find · parser · octavia, mileage_max=150000 | 3 Octavias, 124k–143k km, 27.9k–34.9k | **PASS**. Minor: the wording "koje prelaze do 150.000 km" is awkward. |
| M2a | auto do 20.000 KM | find · parser · budget=20000 | "306 oglasa… do 20.000 KM" | **PASS** |
| M2b | (follow-up) 50.000km do 60.000km | find · parser · **budget=60000, mileage_max=50000** | "23 oglasa u tom rasponu…" + button "📅 Od 1985" | **FAIL**: "km" was meant as KM (a price correction). The bot reads one bound as mileage and the other as a 60k budget, and drops the previous 20k. *Fix branch: yes (km-as-KM after a price question)* |

### Follow-ups that must keep context

| # | Input | Parsed | Reply | Verdict |
|---|---|---|---|---|
| F1a | Golf do 20k | find · parser · golf, budget=20000 | Golfs 2.599–17.900 KM, 1983–2017 | **PASS** |
| F1b | ne, nego dizel | find · gemini · search="golf dizel", budget=20000 | "Evo, na OLX-u ima 306 oglasa do 20.000 KM… Škoda Scala, Ford Focus i Renault Clio" | **FAIL**: "golf dizel" isn't a known model, so it silently searches all models. The phrasing hides that the user is no longer seeing Golfs. *Fix branch: partly (merge yes, fuel filter no)* |
| F1c | a automatik? | find · parser · search="automatik", budget=20000 | "Za automatik do 20.000 KM nema odgovarajućih oglasa" | **FAIL**: Golf (and diesel) is replaced by the word "automatik", so 0 results. *Fix branch: no (no transmission filter)* |
| F1d | isto ali Passat | find · gemini · passat, budget=20000 | "22 oglasa za Passat… do 20.000 KM" | **PASS**: the budget carried over. |
| F2a | Octavia od 2019 do 25000 KM | find · parser · octavia, 25000, 2019 | No fresh results; 11 older ones offered | **PASS** |
| F2b | isto ali Golf | find · gemini · golf, 25000, 2019 | "Za Golf do 25.000 KM i od 2019… 5 starijih oglasa" | **PASS** |

### Specific model / price check

| # | Input | Parsed | Reply | Verdict |
|---|---|---|---|---|
| S1 | Golf 7 2017 dizel, koja je normalna cijena? | find · parser · search="golf 7 dizel normalna", year_min=2017 | "Nisam baš najbolje skužio model pa su ti se tu pojavili neki drugi auti" | **FAIL**: question words get into the search text, so the model is unrecognised and the bot shows random cars. No price answer. *Fix branch: no* |
| S2 | Octavia ili Passat za porodicu? | compare · gemini · [Octavia, Passat] | "Octavia 229 oglasa, medijan 15.950 KM; Passat 332, medijan 15.500 KM" | **WEAK**: grounded, but the medians mix every model year (1.500–89.000 KM range), and "za porodicu" isn't addressed. |
| I4 | is this a good price: Audi A4 2017 22.000 KM | find · parser · audi a4, **budget=22000**, 2017 | "I can't say if that price is good. OLX shows 1 listing… 245.868 km" | **FAIL**: the stated price becomes a cap, so the "comparison" set is just the car itself. There's no median for A4 2017 and no verdict. *Fix branch: no* |
| I5 | da li je 22.000 KM dobra cijena za Audi A4 2017? | same as I4 | "imam samo jedan oglas… uzorak prilično tanak" | **FAIL**: same as I4. |
| E7 | BMW X5 2020 automatic, what's a fair price? | find · parser · search="bmw x5 automatic fair", 2020 | "I couldn't find matches for that exact search, but OLX has a bunch of recent listings" | **FAIL**: same search-text pollution. *Fix branch: no* |
| E11 | koliko košta prosječno Škoda Superb 2019? | find · parser · search="prosječno škoda superb", 2019 | "Nisam uspio prepoznati Škoda Superb" | **FAIL**: "prosječno" gets into the search text. *Fix branch: no* |
| E10 | uporedi Golf 7 i Octavia 3 | compare · parser · [golf 7, octavia 3] | "…ne pokazuju aktivne oglase u zadnjih 90 dana za oba modela" (facts: "Svježina: zapažanja do nepoznato") | **FAIL**: generation suffixes ("7", "3") match nothing, so the bot claims there are no Golfs or Octavias. *Fix branch: no* |

### Intent

| # | Input | Parsed | Reply | Verdict |
|---|---|---|---|---|
| I1 | šta se najlakše prodaje? | liquidity · parser | "Octavia, Audi A3 i Citroën C4 najbrže nestaju… **medijan od 0 dana**" (facts: Octavia vanished 285 of 292, Megane 62 of 62) | **FAIL**: misleading. A median of 0 days with nearly 100% vanishing is collector churn (date-sort page drift), not liquidity. The ranking is an artifact. |
| I2 | treba mi porodični auto do 25k | find · parser · search="treba", budget=25000, family | 127 family-model listings 10.999–24.500 KM; listed **Suzuki Vitara · 3.024.000 km**; button "🛣 Do **2.110.000 km**" | **WEAK**: the family filter works and "treba" is harmlessly ignored, but junk mileage is shown and drives the refine button. |
| I3 | najjeftinije dobre ponude ove sedmice | latest · gemini | "…zanimljivih ponuda… Peugeot 508 za 14.999 KM, Golf 2013 za 13.200 KM…" | **WEAK**: routed to "latest new IDs", not deals and not sorted by price. The phrasing presents first-seen IDs as "offers". |
| E6 | Which cars sell fastest between 20k and 30k? | liquidity · parser · search="between 30k", **budget=20000** | "we don't have enough data… for that price range" | **FAIL**: the range is parsed as search text "between 30k" plus a 20k cap. *Fix branch: yes ("between" range)* |

### Edge cases

| # | Input | Parsed | Reply | Verdict |
|---|---|---|---|---|
| E1 | golff 7 dizl do 18 iljada | find · gemini · search="golf 7", budget=18000 | "nisam uspio prepoznati Golf 7 pa ti prikazujem druge opcije" | **FAIL**: Gemini repaired the typos, but "golf 7" is still unknown (generation suffix), and diesel was dropped. |
| E2 | koji auto da kupim? | clarify · gemini | "Možeš li mi reći koliki ti je maksimalan budžet…" + budget buttons | **PASS** |
| E3 | Tesla 2024 do 10k | find · parser · tesla, 10000, 2024 | "nema odgovarajućih oglasa… probaj proširiti pretragu" + button "📅 Od 2026" | **WEAK**: honest, but it doesn't say the combination is unrealistic, and the "Od 2026" refine button on 0 results makes no sense. |
| E4 | kakvo je vrijeme danas? | unsupported · gemini | "mogu pomoći samo oko auta… vrijeme stvarno ne znam" | **PASS** |
| E5 | What's the cheapest Golf 7 diesel under 15k? | find · parser · search="cheapest golf 7 diesel", 15000 | "**OLX didn't recognise** that specific model search, so it pulled up other listings" | **FAIL**: search pollution plus the generation suffix. The phrasing also wrongly blames OLX. |
| E8 | hvala! | chitchat · gemini | "Nema na čemu! Javi se ako ti zatreba još nešto oko auta." | **PASS** |
| E9 | golf 15 | find · gemini · golf, budget=15000 | 25 Golfs up to 15k, listed | **PASS** |
| E12 | Mercedes C klasa 2015-2018 do 30 hiljada, automatik | find · gemini · search="mercedes c klasa", 30000, year_min=2015 | "Nisam uspio prepoznati Mercedes C klasu" → Corsa, Fabia, Scala… | **FAIL**: data stores models as "C 220" etc., so "C klasa" never matches. The year ceiling and "automatik" are dropped. *Fix branch: year range only* |

Language: every Bosnian message got a Bosnian reply and every English message an English one. Tone is friendly. It greets on every turn ("Ćao!/Hej!/Hejj!"), which gets repetitive inside one conversation.

## Fix-branch coverage (`fix/bot-ranges-followups`)

- **Covered (7):** P1, P2, Y1, C1a (range part), C1b, M2b, E6.
- **Partly covered (2):** C1a (outliers remain) and F1b (merge only).
- **Not covered (13, plus the fuel part of F1b):**
  - "preko/iznad/over": P4
  - fuel and transmission as filters: P3, F1b, F1c
  - question words and generation numbers in search text, and model aliases: S1, E1, E5, E7, E10, E11, E12
  - price-check intent: I4, I5
  - liquidity churn artifact: I1

I recommend re-running this sweep on the fix branch before it merges; the cases are listed above.

## Top 5 issues, by user impact

1. **Model searches fail silently and show unrelated cars** (S1, E1, E5, E7, E10, E11, E12, F1b; 8 cases). Words like "normalna", "prosječno", "cheapest", "fair", "automatic" and "dizel", plus generation numbers ("Golf 7", "Octavia 3") and naming like "C klasa", end up in the search text. `_is_known_model` then fails, and the bot shows all models. Gemini's phrasing often hides this.
   **Fix:**
   - Strip question and attribute words before the model lookup.
   - Drop trailing generation tokens, or use them as a year hint.
   - Retry the lookup with fewer tokens until a known make/model matches.
   - Add a small alias table (C klasa → `C %`, Golf 7 → Golf 2013–2020).
   - If nothing matches, ask "Jesi li mislio na …?" instead of showing every model.

2. **Range parsing: lower bounds, upper years and "preko" are dropped or misread** (P1, P2, P4, Y1, C1a, M2b, E6). The worst case is "Golf 2022-2024" becoming a 2.024 KM budget.
   **Fix:** merge `fix/bot-ranges-followups`, and add "preko/iznad/više od/over/above/from X" as `price_min` with no maximum. P4 is the gap. Add Y1 and P4 to its tests.

3. **Follow-ups lose context, and fuel/gearbox are not filters** (C1b, F1b, F1c, P3). A correction gets "ja ne pratim te detalje", and "dizel"/"automatik" replace the model instead of narrowing it.
   **Fix:** the fix branch's correction merge handles C1b. Separately, add `fuel` and `transmission` to `Filters`, the parser and the Gemini schema, and map dizel/benzin/hibrid/automatik/manuelac to columns. `fuel_type` is filled on 99% of rows; `transmission` on only 19%, so the bot should note that automatic results are incomplete.

4. **"Is this a good price?" has no real answer** (I4, I5, S1, E7, E11). The stated price is used as a cap, so the comparison set is the car itself, and Gemini replies "I can't say".
   **Fix:** add a `price_check` intent (parser cue: "dobra/normalna/fer cijena", "is this a good price", "koliko košta"). Route it to model_stats for model + year ±1, with no price cap. Answer with median, p25–p75 and n, and say where the stated price sits, with a small-sample caveat. This is grounded, so it stays honest.

5. **Misleading data skews answers** (I1, C1a, P1, I2, E3):
   - Liquidity reports "median 0 days" from churn (285 of 292 Octavias "vanished").
   - About 286 new or near-0 km dealer listings (year ≥ 2025, under 1.000 km, about 5% of the last 90 days) top used-car lists, drag the Sportage median to 5.000 km and appear as "33% below median" deals.
   - 12 listings above 1.000.000 km produce buttons like "Do 2.110.000 km".

   **Fix:**
   - Exclude year ≥ current with km under 1.000 from medians and deals (and sort them last) unless the user asks for "novo/new".
   - Treat mileage above 999.999 as unknown.
   - For liquidity, ignore ads that disappeared within one collection run, require a median of at least 1 day, and suppress the answer when the vanish rate is close to 100%.
   - Never offer a refine button outside a plausible range.

### Also worth noting
- **Gemini capacity:** the key returned 429 at about 1 msg/s and later timed out at about 8 s per call. That means up to about 17 s before a fallback answer when both calls fail. Consider a shorter interpret timeout (about 3–4 s), skipping phrasing after an interpret failure, and checking the key's quota tier.
- **Phrasing accuracy:** E5 blames OLX ("OLX didn't recognise…") for the bot's own fallback, and F1b presents all-model results as if they answered "dizel". Add a phrasing rule: when the facts contain "Nisam prepoznao…", the reply must say so in its first sentence.
- **I3:** "najjeftinije/dobre ponude" should map to the existing deal block (suggest's "znatno jeftiniji"), not to `latest`.
