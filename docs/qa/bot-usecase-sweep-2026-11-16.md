# Garaža bot: use-case QA sweep (16 Nov 2026)

**Code under test:** `main` @ `5fd4a10` (PRs #40 and #41 merged, so every fix from the 15 Nov sweeps is in).
**Result:** 44 turns in 38 conversations (38 fresh questions + 6 follow-ups). **16 PASS · 21 WEAK · 7 FAIL.**
None of the 38 questions repeats a case from the three 15 Nov reports.

**Read this first: the run was parser-only.** No `GEMINI_API_KEY` was available, so Gemini's interpret and
phrasing calls never ran. That is the same limit as the 15 Nov re-run 2, and it is the production
fallback path (Gemini 429s and timeouts were seen on 15 Nov). Real users get Gemini first, so some WEAK
rows, mostly vague and out-of-scope ones, may read better in production. The FAILs below are in the
deterministic parser and filters, and Gemini would only help if it happened to rewrite the question.

## How it was run (read-only)

- `telegram_bot.process_update` with a fake Telegram transport. Each conversation is its own private
  chat with an allowlisted QA user id, so follow-ups use the bot's real scope and history memory. Nothing was sent to Telegram.
- Production Supabase through the backup role. The secret was streamed over ssh stdin into the process and never written to disk
  or printed. The engine was created with `default_transaction_read_only=on`; the pipeline's own SQL was unchanged.
- `telegram_log.record` was replaced with a no-op, so nothing was written to `bot_interactions`.
- Data snapshot: observations up to 16 Nov, 08:18. Every turn answered in under 2 s, with no exceptions.
- Grading: **PASS** = right filters and a useful, honest answer. **WEAK** = honest but unhelpful, or a gap that
  the user can see and work around. **FAIL** = wrong filters or a misleading answer.

## Results

### Prices for a model and year

| # | Input | Parsed | Reply (short) | Verdict |
|---|---|---|---|---|
| 1 | koliko košta Golf 6 2010? | price_check · golf · 2009–2011 | 146 comparable ads, median 10.925 KM, middle half 9.500–12.788 | **PASS** |
| 2 | Audi A3 2016 cijena | find · audi a3 · from 2016 | One listing (20.990 KM, 171.500 km) plus "10 older". No price statistics | **WEAK**: "cijena" is not a price question, and "2016" means "from 2016". `koliko košta Audi A3 2016` gives a proper check (17 ads, median 22.200) |
| 3 | what does a 2018 BMW 320d cost? | find · bmw 320d · from 2018 | One result: a 2024 BMW 320D, 69.800 KM, 36.200 km | **FAIL**: an English price question is not recognised, and the only hit is a 6-years-newer car at 3x the price |
| 4 | Opel Astra 2012 do 9000 KM | find · astra · ≤9000 · from 2012 | None fresh, 8 older offered | **PASS** |
| 5 | Renault Clio 2019 | find · clio · from 2019 | None fresh, 8 older offered | **PASS** |

### Ranges and corrections

| # | Input | Parsed | Reply (short) | Verdict |
|---|---|---|---|---|
| 6 | Passat između 15 i 20 hiljada | passat · 15.000–20.000 | 8 fresh, 11k–20k | **PASS** |
| 7 | Octavia 2014-2016 od 12000 do 16000 KM | search `octavia 16000`, **budget=12000**, 2014–2016 | "Nisam prepoznao „octavia 16000“…", none fresh | **FAIL**: the lower bound became the ceiling, and the upper bound leaked into the search text |
| 7b | (follow-up) a ne, do 14000 | budget=14000, same polluted search | One 2016 Octavia, 12.950 KM | **WEAK**: the answer is right but the "nisam prepoznao" note stays and the 12.000 floor is gone |
| 7c | (follow-up) mislio sam 2017 | year_min=2017, budget=14000 | "Nema odgovarajućih oglasa u zadnjih 90 dana", cheapest real match 16.500 KM | **WEAK**: the correction works and the nearest-match answer is honest, but the chain started broken (row 7) and the old year span is silently replaced |
| 8 | auta od 8 do 12 hiljada maraka | 8.000–12.000 | 90 fresh, listed | **PASS** |

### Comparisons ("za porodicu")

| # | Input | Parsed | Reply (short) | Verdict |
|---|---|---|---|---|
| 9 | Škoda Octavia vs VW Golf za porodicu | compare | Like-for-like years 2009–2019, medians 12.974 vs 15.400, per-band medians | **WEAK**: the data is right, but "za porodicu" is ignored (known open item from 15 Nov) |
| 10 | šta je bolje za porodicu: Passat ili Superb? | compare | 2011–2020, 215 vs 33 ads, medians 19.500 vs 26.000 | **WEAK**: same; a "which is better" question gets prices only, and the thin Superb sample (33) is not flagged |
| 11 | compare Audi A4 and BMW 3 series | compare (EN) | 2006–2016, 122 vs 65 ads, medians 15.300 vs 14.900 | **PASS** |
| 12 | Kia Sportage ili Hyundai Tucson 2019 | find · `kia sportage hyundai tucson` | "Nisam prepoznao…, pa tražim „kia sportage“", none fresh | **FAIL**: not seen as a compare, and half of the question is dropped |

### Fuel and transmission

| # | Input | Parsed | Reply (short) | Verdict |
|---|---|---|---|---|
| 13 | Golf 7 dizel automatik do 20k | golf · 2012–2020 · diesel · automatic · ≤20k | None fresh, 8 older; notes that gearbox is filled for about 1 in 5 ads and that 14 match without the gearbox filter | **PASS** |
| 14 | benzinac do 10000 KM | petrol · ≤10k | 27 fresh, listed | **PASS** |
| 15 | petrol automatic under 15k | petrol · automatic · ≤15k | 1 fresh (Nissan Terrano 1988), 10 older, gearbox caveat | **PASS** |
| 16 | hibrid do 30 hiljada | hybrid · ≤30k | None fresh, 3 older, 1 new-car note | **PASS** |
| 17 | električni auto do 25k | electric · ≤25k | None fresh, 1 older | **PASS** |

### Mileage and km

| # | Input | Parsed | Reply (short) | Verdict |
|---|---|---|---|---|
| 18 | Passat B8 do 120 hiljada kilometara | search `passat b kilometara`, **budget=120000, price_min=8000** | "8.000–120.000 KM", 2 ads at 235k–320k km | **FAIL**: "hiljada kilometara" is read as KM, "B8 do 120" as a price range, and the km limit is lost |
| 19 | auto sa malo kilometara do 20k, manje od 80000 km | search `kilometara`, ≤20k, ≤80.000 km | "Nisam prepoznao „kilometara“ kao marku", no cars shown, "did you mean Suzuki Vitara…" | **FAIL**: filters are right but the stray word cancels the whole search |
| 20 | BMW X3 preko 200.000 km | bmw x3 · **mileage_max=200000** | "do 200.000 km", none fresh, 13 older | **FAIL**: "preko" (over) is turned into "up to". `Filters` has no `mileage_min` |

### Typos and slang

| # | Input | Parsed | Reply (short) | Verdict |
|---|---|---|---|---|
| 21 | pasat 2015 dizl | `pasat` · diesel · from 2015 | None fresh, 2 older | **WEAK**: "pasat" is searched literally. `passat 2015 dizel` finds 12 fresh. The bot shows "pasat" in the title and does not say it found fewer |
| 22 | oktavija do 12 iljada | `oktavija` → octavia, ≤12k | 9 fresh, listed, note "tražim „octavia“" | **PASS** |
| 23 | vw polo za grad do 11k, benca | search `polo grad benca` → polo, ≤11k | 6 fresh | **WEAK**: "benca" (petrol) is dropped without a word; `vw polo benzinac do 11k` finds 2 |
| 24 | audi a6 3.0 tdi quattro | audi a6 · diesel | 19 fresh, any engine | **WEAK**: tdi is understood, "3.0" and "quattro" are dropped; the note says so |
| 25 | bmw 320 2014 kolko je | `bmw 320 kolko` → bmw 320 · from 2014 | A 2024 BMW 320D and a 2014 BMW 320 listed | **WEAK**: "kolko je" is not a price question and "2014" means "from 2014", as in row 3 |

### Vague and out-of-scope (parser fallback only)

| # | Input | Parsed | Reply (short) | Verdict |
|---|---|---|---|---|
| 26 | nešto jeftino i pouzdano | find · `pouzdano` | "Nisam prepoznao „pouzdano“… Jesi li mislio na Fiat Punto?" | **WEAK**: honest, but the Punto suggestion is noise; it should ask for a budget |
| 27 | koji auto je najbolji za početnike? | find · `početnike` | "Nema odgovarajućih oglasa… „početnike“ se ne poklapa ni s jednom markom" | **WEAK**: no scope message, no examples |
| 28 | show me something nice | find · `nice` | "I didn't recognise “nice”… Did you mean Renault Scenic, Kia Stonic or Mercedes-Benz CE?" | **WEAK**: same; random suggestions |
| 29 | kako da registrujem auto u BiH? | find · `kako registrujem` | "Did you mean Škoda Karoq, Ford Ka or Renault Kangoo?" | **WEAK**: out of scope, but the bot never says so (weather has a scope message, this does not) |
| 30 | koliki je kredit za auto na 5 godina? | find · `koliki kredit 5` | "Jesi li mislio na BMW X5, Volkswagen T5 ili Audi S5?" | **WEAK**: the digit 5 matches model names |
| 31 | daj mi recept za ćevape | find · `recept ćevape` | "Jesi li mislio na Jeep Grand, Jeep wrangler ili Kia cee'd?" | **WEAK**: no harm done, but the suggestions look silly |
| 32 | Yamaha R6 motocikl do 8000 | find · `yamaha r6 motocikl`, ≤8000 | "Jesi li mislio na Audi RS6?" | **WEAK**: motorcycles are out of scope, but the answer implies a typo |
| 33 | Tesla Model 3 cijena | model_stats · `tesla 3` | "Nema odgovarajućih oglasa u zadnjih 90 dana" | **WEAK**: there are 2 Teslas in the 90-day window (ads list the model as "Model"). `tesla` alone finds them, but "Model 3" gives a flat "none" |

### Follow-ups

| # | Input | Parsed | Reply (short) | Verdict |
|---|---|---|---|---|
| 34 | Golf 7 do 18k | golf · **7.000–18.000 KM** (no 2012–2020 year range) | 26 fresh, any Golf generation, title "7.000–18.000 KM" | **FAIL**: "7 do 18k" is read as a price range. See "Root cause A" below |
| 34b | (follow-up) a Passat? | passat · 7.000–18.000 | 22 fresh | **PASS**: carry-over works (inherits the row 34 floor, counted once) |
| 34c | (follow-up) samo automatik | + automatic | 1 fresh (17.990 KM), 17 older, gearbox caveat | **PASS** |
| 34d | (follow-up) pokaži još | unsupported | "To ne mogu povezati s nečim na šta naša baza može odgovoriti" | **WEAK**: the previous answer had only one listing, so the right reply is "nema više", not "unsupported" |
| 35 | koji se najbrže prodaje? | liquidity | "Iz ovih podataka ne mogu poredati šta se najbrže prodaje: 96% oglasa … kasnije je nestalo" | **WEAK**: honest, and better than the 15 Nov "median 0 days", but the user gets no alternative |
| 35b | (follow-up) a do 15 hiljada? | liquidity · ≤15k | Same refusal, scoped to ≤15.000 KM | **WEAK**: the follow-up scope carries over; same dead end |

### Other

| # | Input | Parsed | Reply (short) | Verdict |
|---|---|---|---|---|
| 36 | is a 2016 A4 at 17.500 a good deal? | price_check · a4 · 2015–2017 | 26 ads, median 26.450, "34% below the median" | **PASS** |
| 37 | šta ima novo danas? | latest | Last pass summary, new IDs (Ioniq, i20, Tucson 2026) | **PASS**: one of the listed new cars has no price ("cijena …") and the rest are 2026 new cars |
| 38 | Mercedes E klasa 2012 dizel koliko km ima prosječno? | price_check · e klasa · diesel · 2011–2013 | Price median 21.900 KM (15 ads) | **WEAK**: the question was about average mileage, and the answer is a price check |

## Extra checks (not graded)

A second batch of 15 messages confirmed the root causes of the FAILs:

| Input | What it shows |
|---|---|
| Golf 6 do 12k → 6.000–12.000 KM · Golf 7 do 18k KM → 7.000–18.000 · Golf 8 do 25k → 8.000–25.000 | The generation digit becomes the price floor; same with `Passat B8 do 120 hiljada` (floor 8.000) |
| Passat 2014-2016 od 15000 do 20000 KM → budget=15.000, search `passat 20000` | The row 7 bug again: a price range after a year span |
| BMW X3 sa više od 200000 km · Octavia sa preko 300 hiljada km | Both become "up to"; Octavia shows 14 cars with the wrong mileage direction |
| passat 2015 dizel (12 fresh) vs pasat dizel od 2015 (0 fresh, 2 older) | A one-letter typo hides 10 listings and nothing tells the user |
| koliko košta BMW 320d 2018 → price_check, but widened to 2016–2020 on just 2 comparable ads | A price question works with "koliko košta", the sample warning is shown |
| Golf 7 do 18k, then "pokaži još" | The follow-up is "unsupported" even though a page of 5 of 26 was shown |

## Top findings

1. **"Golf 7 do 18k" is wrong (rows 34, 18; extra checks).** A generation or model number followed by "do N" is read as a
   price range (7–18k, 8–25k), and the generation's year range is never applied. This is a very common way to ask. The reply looks plausible
   and the only hint is "7.000–18.000 KM" in the title.
2. **Mileage phrasing is mostly unsupported (rows 18, 19, 20).** "hiljada kilometara" becomes KM, "malo kilometara" kills the search, and
   "preko/više od N km" is inverted to "up to N" because `Filters` has no `mileage_min`.
3. **A price range after a year range is misparsed (row 7).** "2014-2016 od 12000 do 16000 KM" gives budget=12.000 and `octavia 16000`
   as the search text. The next turn's correction ("a ne, do 14000") then only half repairs it.
4. **Price questions are matched only on "koliko košta" (rows 2, 3, 25, 38).** "cijena", "what does … cost", "kolko je" give a listing page,
   and a listing page with "2018" read as "from 2018" can surface a 2024 car. "koliko km ima prosječno" gets a price answer.
5. **Without Gemini, non-car text gets random model suggestions (rows 26–32).** Weather has a scope message, but "kredit",
   "registracija", "recept" and "motocikl" get "Jesi li mislio na BMW X5 / Jeep Grand / Audi RS6?", so the bot never says it is out of scope.

Also worth knowing: the typo "pasat" hides most Passat results (row 21), "benca" is silently ignored (row 23), "A ili B" with two
different makes is not a compare (row 12), and "Tesla Model 3" says "none" while 2 Teslas exist (row 33).

## Suggested improvements, ranked

| Rank | Improvement | Why | Rows |
|---|---|---|---|
| 1 | In `_price_range`, never take a generation or model number ("golf 7", "passat b8") as one end of a range; apply that generation's years instead | The most natural question shape is wrong today | 34, 34b, 34c, 18 |
| 2 | Add `mileage_min`, read "preko / više od / od N km" (and "N hiljada km / kilometara") as mileage, and make "malo / puno kilometara" a mileage hint (not search text) | Four mileage phrasings fail; the filter model cannot express "over" | 18, 19, 20 |
| 3 | Parse "od A do B KM" after a year span, and remove numbers already used by a filter from the search text (`octavia 16000`, `passat 20000`) | Stops a range that flips and the "nisam prepoznao" note on correct searches | 7, 7b, 7c |
| 4 | Widen price-check triggers ("cijena", "what does … cost", "how much", "kolko je", "prosječna cijena") and treat a bare year as that year ±1 rather than "from" | A price question should get price statistics, not the one newest listing | 2, 3, 25 |
| 5 | When the parser finds no model, no filters and the text is not close to a model, say the bot only covers used cars from olx.ba (with the example buttons) instead of "did you mean" guesses; skip suggestions below a similarity floor and ignore digits | Out-of-scope and vague questions in the fallback path | 26–32 |
| 6 | Typo tolerance for model names ("pasat", "golff") through the alias table or pg_trgm; show "tražim passat" when the match is fuzzy | One missing letter hides 80% of the listings | 21 |
| 7 | Compare: accept "A ili B" with two makes, and add one line for "za porodicu" (both on the family list, wagon share, trunk/size are not in the data, so say so) | Closes the 15 Nov open item and the Kia/Hyundai case | 9, 10, 12 |
| 8 | Add petrol slang ("benca", "benzinac" is done, "plin" for LPG) and engine tokens ("3.0", "quattro") as ignored hints with a note | Silent drops on fuel are the worst kind | 23, 24 |
| 9 | Liquidity: when the disappearance data cannot rank, offer a proxy (the most-listed models for the filter, or the newest ads) rather than only a refusal | The user leaves empty-handed twice in a row | 35, 35b |
| 10 | Treat "pokaži još / more / dalje" as a next page, or say "nema više" when the last page was complete | A natural follow-up falls into "unsupported" | 34d |
| 11 | "koliko km ima prosječno": answer with median mileage for the cohort | Wrong stat type for the question | 38 |
| 12 | "Tesla Model 3" (and similar where the ad title keeps "Model"): fall back to the make when no model row matches, as `tesla` does | A flat "none" while 2 Teslas exist | 33 |

## Not covered

- Gemini interpret and phrasing (this run is parser-only), the Telegram group path (mentions, group window), button taps,
  and the weekly digest and follow commands.
- Anything about data freshness beyond what the answers show. The data was current to 16 Nov, 08:18.
