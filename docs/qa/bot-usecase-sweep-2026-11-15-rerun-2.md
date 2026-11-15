# Garaža bot: second sweep re-run on `fix/bot-qa-sweep-3` (15 Nov 2026)

The same 37 turns as [bot-usecase-sweep-2026-11-15.md](bot-usecase-sweep-2026-11-15.md) and the
[first re-run](bot-usecase-sweep-2026-11-15-rerun.md), run the same read-only way:
- `telegram_bot.process_update` with a fake Telegram transport, private chats, follow-ups in the same chat
- production Supabase through the backup role, secret streamed over stdin, every transaction `READ ONLY`
- `telegram_log.record` disabled
- **parser-only**: no `GEMINI_API_KEY` was in the environment, so there were no interpret or phrasing calls

Both sides ran back to back on the same data snapshot (observations up to 15 Nov, 08:18).

| | PASS | WEAK | FAIL |
|---|---|---|---|
| `main` `99c5901` (after #40), parser-only | 32 | 5 | 0 |
| this branch, parser-only | **37** | **0** | **0** |

The other 32 turns gave **byte-identical** replies and buttons on both sides. Every turn answered in
at most 1.6 s; there were no errors.

## The five cases

| # | Input | main → branch | Branch answer (short) |
|---|---|---|---|
| S2 | Octavia ili Passat za porodicu? | WEAK → PASS¹ | "Upoređena godišta: 2009–2020, godišta koja oba modela imaju u našim podacima." Octavia 172 ads, median 14.225 KM; Passat 269, median 15.900 KM; then medians per band: 2009–11 9.000 / 11.950 · 2012–14 12.800 / 14.700 · 2015–17 17.999 / 22.150 · 2018–20 22.450 / 32.450. The 🔎 buttons search those years. (main: all years mixed, 229 vs 340 ads) |
| I3 | najjeftinije dobre ponude ove sedmice | WEAK → PASS | "🏷 Dobre prilike": 7 listings at least 15% under the median for the same model and year, seen in the last 7 days, cheapest first, each with % under the median and its sample size; ⭐ save buttons. (main: "can't map that") |
| E3 | Tesla 2024 do 10k | WEAK → PASS | "Nema odgovarajućih oglasa… Ništa poput „tesla · do 10.000 KM · od 2024“ ne postoji u našim podacima. Najjeftiniji stvarni oglas (tesla, sva godišta i cijene) je Tesla Model · 2021 · 82.747 km za 55.000 KM. Tipična tražena cijena: medijan 72.000 KM (2 oglasa)." No 2024 Tesla exists, so it falls back to the model. Button: 🔎 tesla. |
| E4 | kakvo je vrijeme danas? | WEAK → PASS | "Vrijeme ne pratim, samo polovna auta s olx.ba. 🙂" plus one line on what the bot can do, and the three example buttons. No Gemini needed. (main: searched for a model called "vrijeme") |
| E9 | golf 15 | WEAK → PASS | "Viđeni oglasi · golf · 2015": 5 fresh Golfs, all 2015 (34 older hidden). No Gemini needed. (main: "Nisam prepoznao „golf 15“", every Golf) |

¹ S2 now compares like with like, which was the gap this branch targets. "za porodicu" is still not
addressed on its own; both models are on the family list, so the answer is not wrong, but it doesn't say so.

## What changed (summary)

- **Compare** (`telegram_bot.compare`): the user's years when given ("Octavia ili Passat 2016-2019?");
  otherwise one named generation's years for both ("Golf 7 vs Passat"); otherwise the years both models
  usually have (10th–90th percentile of each, overlapped, at least 10 listings each). Two named
  generations stay each in its own, as before (E10 unchanged). Wide spans also show medians per band.
- **Deals** (`deals` intent, `telegram_bot.deals_message`): "dobre ponude / prilike", "povoljno", "good deals",
  "bargains" reuse the shortlist's standouts query (now `_standouts`), filtered by model, price, year and fuel.
  "ove sedmice" means 7 days, otherwise 14; "najjeftinije" sorts by price. "je li ovo dobra ponuda: …" stays a
  price check. A reply narrows it ("a dizel?").
- **Unrealistic search** (`_nearest_real`): when a search finds nothing in 90 days (not just nothing fresh),
  the answer says nothing like it exists and gives the cheapest real match and the median, first without the
  price bounds, then for the model in any year. Placeholder prices (under 35% of the median) are skipped.
- **Small talk** (`small_talk_topic`): greetings, weather, "kako si", "ko si ti / šta možeš", jokes; up to 7
  words and no digits. Gemini still gets the first go when configured; its wording wins.
- **Short years** (`short_model_year`): a two-digit 00–30 right after a model word is a model year, unless it is
  one of that model's generations or a unit / "k" / "godina" follows. Shown as a single year in the title.
- The 2025+ under-1.000 km new-car rule is unchanged.

## Still open

- S2: "za porodicu" is not addressed in a compare answer (see ¹).
- E3: the "typical price" is honest but thin when the model has few ads (2 Teslas here).
- None of this was run with Gemini on; Gemini paths are covered by unit tests only.
