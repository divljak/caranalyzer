# Garaža bot: sweep re-run on `fix/bot-qa-sweep-2` (15 Nov 2026)

Same 37 turns as [bot-usecase-sweep-2026-11-15.md](bot-usecase-sweep-2026-11-15.md), same read-only method:
- `telegram_bot.process_update` with a fake Telegram transport, private chats, follow-ups in the same chat
- production Supabase through the backup role, secret streamed over stdin, every transaction `READ ONLY`
- `telegram_log.record` disabled

**Difference from the original run: no Gemini.** No API key was available for this run, so both sides
below are **parser-only** (deterministic parse → DB answer, no interpret or phrasing call). The original
report (12 PASS · 4 WEAK · 21 FAIL on `4c2f3f7`) had Gemini on. To keep the comparison fair, `origin/main`
(`1e34d99`, which already includes #39) was re-run the same way just before this branch.

| | PASS | WEAK | FAIL |
|---|---|---|---|
| Original report, `4c2f3f7`, Gemini on | 12 | 4 | 21 |
| `origin/main` `1e34d99`, parser-only | 12 | 4 | 21 |
| this branch, parser-only | **32** | **5** | **0** |

Verdicts use the original report's criteria. "→" is main → branch.

| # | Input | main → branch | Branch answer (short) |
|---|---|---|---|
| P1 | auto od 50 - 60k | WEAK → PASS | 50.000–60.000 KM; the 2026 Octavia at 1 km no longer tops the list ("Izostavljeno: 38 novih auta") |
| P2 | nešto između 20 i 25 hiljada KM | PASS → PASS | 20.000–25.000 KM |
| P3 | dizel do 15.000 KM | FAIL → PASS | fuel filter: 190 diesels ≤15k (main: "dizel" as a model, 0) |
| P4 | auta preko 40k | FAIL → PASS | "od 40.000 KM"; budget buttons 50/60/80k (above the minimum) |
| Y1–Y3 | year ranges | PASS → PASS | unchanged |
| C1a | …najbolje prodaje od 50 - 60k, godiste od 2022 - 2024 | PASS → PASS | both ranges kept; no 0 km deal |
| C1b | Mislim da sam stavio gore od 50.000km… | PASS → PASS | correction keeps both ranges |
| M1 | Octavia do 150.000 km | WEAK → PASS | the 2026 Octavia at 1 km is left out (noted) |
| M2a | auto do 20.000 KM | PASS → PASS | |
| M2b | 50.000km do 60.000km (after a price question) | FAIL → PASS | 50.000–60.000 KM (main: mileage ≤60.000 km, lower end dropped) |
| F1a | Golf do 20k | PASS → PASS | |
| F1b | ne, nego dizel | FAIL → PASS | golf · dizel · do 20.000 KM, 36 listings |
| F1c | a automatik? | FAIL → PASS | golf · dizel · automatik: none fresh, gearbox caveat, "Bez filtera mjenjača ima 36", buttons for any gearbox / older |
| F1d | isto ali Passat | FAIL → PASS | passat · dizel · automatik · do 20.000 KM (main parser: "isto passat", 0) |
| F2a | Octavia od 2019 do 25000 KM | PASS → PASS | |
| F2b | isto ali Golf | FAIL → PASS | golf · do 25.000 KM · od 2019 (main parser: "isto golf", 0) |
| S1 | Golf 7 2017 dizel, koja je normalna cijena? | FAIL → PASS | price check: golf · dizel · 2016–2018, 45 ads, median 23.500, middle half 20.500–24.999 |
| S2 | Octavia ili Passat za porodicu? | FAIL → WEAK | compare Octavia vs Passat (medians mix all years; "za porodicu" not addressed) |
| I1 | šta se najlakše prodaje? | FAIL → PASS | ranking suppressed: 95% of ads seen later left our search, so it measures the collector, not sales |
| I2 | treba mi porodični auto do 25k | PASS → PASS | family list; mileage over 999.999 km is now unknown (none on today's first page either side) |
| I3 | najjeftinije dobre ponude ove sedmice | FAIL → WEAK | "can't map that" + example buttons (not routed to the deal block) |
| I4 | is this a good price: Audi A4 2017 22.000 KM | FAIL → PASS | 24 comparable (2016–2018), median 28.300, middle half 25.500–31.925; "22.000 KM is below the middle half (22% below the median)" |
| I5 | da li je 22.000 KM dobra cijena za Audi A4 2017? | FAIL → PASS | same, in Bosnian |
| E1 | golff 7 dizl do 18 iljada | FAIL → PASS | golf · dizel · do 18.000 KM · 2012–2020, 13 listings |
| E2 | koji auto da kupim? | PASS → PASS | |
| E3 | Tesla 2024 do 10k | WEAK → WEAK | honest no-match; the "Od 2026" button is gone, but it still doesn't say the combination is unrealistic |
| E4 | kakvo je vrijeme danas? | WEAK → WEAK | parser-only: "vrijeme" matches no make/model + hint (Gemini handles small talk) |
| E5 | What's the cheapest Golf 7 diesel under 15k? | FAIL → PASS | golf · diesel · ≤15.000 · 2012–2020, 5 listings |
| E6 | Which cars sell fastest between 20k and 30k? | FAIL → PASS | range kept; ranking suppressed for churn, said plainly |
| E7 | BMW X5 2020 automatic, what's a fair price? | FAIL → PASS | 12 comparable 2019–2021, median 89.800; says the gearbox filter was dropped (too few recorded) |
| E8 | hvala! | PASS → PASS | no reply needed |
| E9 | golf 15 | FAIL → WEAK | "Nisam prepoznao „golf 15“, pa tražim „golf“" (15 is not read as 15k without Gemini) |
| E10 | uporedi Golf 7 i Octavia 3 | FAIL → PASS | golf 7 (2012–2020): 254 ads, median 17.990; octavia 3 (2012–2020): 120 ads, median 17.245 |
| E11 | koliko košta prosječno Škoda Superb 2019? | FAIL → PASS | price check 2018–2020: 10 ads, median 36.950 |
| E12 | Mercedes C klasa 2015-2018 do 30 hiljada, automatik | FAIL → PASS | alias matches "C 220/C 200…"; none with automatic recorded; "Bez filtera mjenjača ima 3 … iz zadnjih 90 dana" + button |

Latency: every turn answered in at most 0.7 s (no Gemini calls).

Still open (not in this branch's scope): I3 should route "dobre ponude" to the deal block; E3 could say a
2024 Tesla under 10k is unrealistic; S2 compares all model years. With Gemini on, E4/E9/I3 are normally
handled by the interpreter. The Gemini half of this branch (time budget, 429 back-off, phrasing checks) is
covered by unit tests only, not by a live run.
