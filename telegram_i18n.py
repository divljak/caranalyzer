"""English and Bosnian wording for the Telegram bot, plus light language detection.

English is the reference text (it is what the bot has always said). Bosnian is written the way
people actually talk to each other in chat: ijekavian, informal "ti", short sentences.
Nothing here touches the database or the network.
"""

import re

LANGUAGES = ("en", "bs")
DEFAULT_LANGUAGE = "en"

BS_MONTHS = ("jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec")
BS_STATUS = {"completed": "ZAVRŠENO", "partial": "DJELIMIČNO", "failed": "NEUSPJELO"}

STRINGS = {
    "en": {
        "unknown": "unknown",
        "price_unavailable": "price unavailable",
        "unparsed_model": "Unparsed model",
        "looking": "🔎 Looking…",
        "chat_not_set_up": "I'm not set up to answer in this chat yet (chat id: {chat_id}). The owner needs to add that id to the TELEGRAM_CHAT_ID setting in Vercel (separated by a comma from the current one) and redeploy.",
        "db_unavailable": "The database is temporarily unavailable, so I can't answer right now. Please try again shortly.",
        # status
        "status_none": "Car Analyzer has no finished collector run yet.",
        "status_title": "Car Analyzer · {quality}",
        "status_latest_attempt": "Latest attempt: {when}",
        "status_counts": "{obs} observations · {new} first observed by us · {errors} errors",
        "status_visits": "Source visits: {search} search · {detail} detail ({done} completed)",
        "status_latest_obs": "Latest stored observation: {when}",
        "status_clean": "Last fully clean run: {when}",
        "status_observed_attempt": "Latest attempt with observations: {when} ({status}, {n} observations)",
        "status_note1": "Freshness describes our observations, not when ads were posted.",
        "status_note2": "Search absence is not evidence that a car sold.",
        # trends
        "trends_title": "Observed asking-price changes · last {days} days",
        "trends_sample": "Sample: {sample} listings observed; {priced} with usable prices",
        "trends_transitions": "Recorded transitions: {drops} drops · {rises} increases",
        "fresh_line": "Freshness: observations through {when}",
        "trends_note": "This is a bounded observed sample, not a full-market index. Asking prices are not sale prices.",
        "trends_usage": "Use /trends DAYS with a number from 1 to 90.",
        # budget (legacy command)
        "budget_usage": "Use /budget AMOUNT [make or model], for example: /budget 25000 Golf",
        "budget_title": "Recently observed at or below {amount}",
        "budget_showing": "Showing {n} database matches:",
        "budget_footer": "These are observed listings, not purchase recommendations or verified availability.",
        "observed_row": "  Observed {when} · {url}",
        "no_match_90": "No matching listings in the last 90 days.",
        # compare / model stats
        "compare_usage": "Use /compare MAKE MODEL | MAKE MODEL, for example: /compare VW Golf | Škoda Octavia",
        "compare_title": "Database comparison · observed in the last 90 days",
        "cohort_sample": "Sample: {n} listings · {priced} priced",
        "cohort_median": "Median asking: {median} · range {low}–{high}",
        "cohort_thin": "Thin sample: fewer than 5 priced listings, so treat these figures with caution.",
        "compare_note": "Same broad 90-day window; no mileage/year normalization. Asking prices are not sale prices.",
        "stats_title": "Asking prices · {label} · observed in the last 90 days",
        "stats_evidence": "Database evidence",
        "stats_note": "No mileage/year normalization. Asking prices are not sale prices.",
        # freshness
        "fresh_none": "The database has no stored observations yet, so there is nothing to answer from.",
        "fresh_stale": "Stale data: the newest stored observation is {days} days old ({when}).",
        "fresh_run": "The latest collector attempt was {status}, so coverage may be incomplete.",
        # find
        "find_title": "Observed listings",
        "title_upto": "up to {x}",
        "title_from": "from {x}",
        "no_more": "No more matches.",
        "search_ignored": "I didn't recognise “{term}” as a car make or model, so I searched all models.",
        "find_hint": "If that was not a car make or model, send /help to see what I can answer.",
        "find_showing": "Showing {span} of {total} database matches, newest model year first, then recently seen, then lowest mileage:",
        "seen_row": "Seen {age} ({when})",
        "old_warning": "⚠ Last seen more than {days} days ago: the ad may no longer be listed. Check the link before relying on it.",
        "filters_note": "Listings without a recorded year or mileage are not included when those filters are used.",
        "find_footer": "These are observed asking prices, not recommendations or verified availability.",
        # suggest
        "suggest_title": "Data-based shortlist",
        "suggest_models_h": "Most listed models in this budget (ads seen in the last 90 days):",
        "suggest_model_row": "{label} · {n} listings · median {price}{km} · newest {year}",
        "suggest_km": " · median {km} km",
        "suggest_notenough": "Not enough listings to compare models here (at least {n} per model needed).",
        "suggest_standouts_h": "Priced well below similar listings (same model and year, at least 15% under the median, mileage at or under the median, seen in the last {days} days):",
        "suggest_standout_row": "{label} · {year} · {price} ({pct}% under the {med} median of {n} similar) · {km} km",
        "suggest_none": "No fresh listing stands out right now.",
        "suggest_foot1": "Most listed means the most ads in our database, not best selling: we have no sales data.",
        "suggest_foot2": "A low price next to similar ads can also mean damage, a quick sale or a wrong entry. Check each ad. These are observed asking prices, not recommendations.",
        # chat
        "chitchat": "Hi! I answer questions about used cars from our olx.ba database, for example “Golf under 15000 KM”, “how much is an Octavia” or “Golf vs Octavia”. What are you looking for?",
        "clarify_suffix": "(I can only show what our database has observed; I can't pick a car for you.)",
        "clarify_currency": "Prices in our database are asking prices in KM. Please give the amount in KM, e.g. “Golf under 15000 KM”.",
        "clarify_default": "I can't pick a car for you; I can only show what our database has observed.\n\nTell me a budget and, if you like, a model, year or mileage limit. For example:\n• Golf under 15000 KM\n• Octavia from 2016 under 150000 km\n• compare Golf vs Octavia",
        "unsupported_forecast": "I don't forecast prices or markets. I can only report what our database has observed, e.g. “trends last 30 days” or “how much is a Golf”.",
        "unsupported_default": "I couldn't match that to something our database can answer, so I won't guess.\n\nI can answer questions about: status/freshness, latest finds, price changes (e.g. “trends last 30 days”), listings under a budget (“Golf under 15000 KM”), a model's asking prices (“how much is an Octavia”), and comparisons (“Golf vs Octavia”). Reply to one of my answers to refine it.",
        # latest
        "latest_none": "No collector run with observations is available.",
        "latest_title": "Latest observed run · {status} · {when}",
        "latest_counts": "{obs} observations · {new} IDs first observed by us · {errors} errors",
        "latest_few": "A few IDs first observed by us in this run:",
        "latest_no_ids": "No first-observed IDs were attributable to this run window.",
        "latest_note": "First observed by us does not mean newly posted on OLX.",
        # help
        "help": "Car Analyzer reads our Supabase evidence only.\n\n/status — collector health and freshness\n/latest — latest observations and a few finds\n/trends 7 — observed price changes (1–90 days)\n/budget 25000 Golf — recently observed matches\n/compare Volkswagen Golf | Škoda Octavia — broad database comparison\n/ask Golf under 15000 KM from 2016 — ask in plain English or Bosnian\n/follow Golf — follow a model\n/following — what you follow and what's new\n/shortlist — the cars you saved\n/help — show this guide\n\nYou can also @mention me or reply to one of my answers with a question, e.g. “Octavia under 20000 KM”, “how much is a Golf”, “Golf vs Octavia”, or a refinement such as “from 2018”. Database coverage is bounded; the bot never treats first seen as posted or absence as sold.",
        "welcome": "Hi! I'm Garaža. Ask me about used cars from our olx.ba database, in English or Bosnian. Tap an example or just write.",
        # buttons
        "btn_status": "Status",
        "btn_latest": "Latest",
        "btn_trends7": "7-day trends",
        "btn_trends30": "30-day trends",
        "btn_market": "Open Market",
        "btn_more": "Show more ({a}–{b})",
        "btn_show_first": "Show listings ({a}–{b})",
        "btn_cheaper": "Budget −10%",
        "btn_pricier": "Budget +10%",
        "btn_newer": "📅 From {year}",
        "btn_lowkm": "🛣 Up to {km} km",
        "btn_budget": "{amount} KM",
        "btn_ex_find": "🔎 Golf under 15000 KM",
        "btn_ex_suggest": "💡 What should I buy for 30000 KM",
        "btn_ex_latest": "🆕 What's new",
        # following
        "follow_ask": "Follow which model? Tap the one you mean. Following shows on the web Following page too.",
        "follow_ask_one": "Follow {label}? Following shows on the web Following page too.",
        "follow_none": "I couldn't find a model like “{query}” in our listings. Try the make and model, e.g. “Volkswagen Golf”.",
        "follow_done": "✅ Now following {label}. Say “what am I following” any time to see what's new on it.",
        "follow_exists": "You're already following {label}.",
        "follow_failed": "I couldn't save that right now. Please try again shortly.",
        "unfollow_done": "Stopped following {label}.",
        "unfollow_gone": "That model is no longer on your list.",
        "following_empty": "You're not following any model yet. Say “follow Golf” to start.",
        "following_title": "Followed models",
        "following_row": "• {label} · {n} listings now · median {median} · {new} first seen since you followed ({when})",
        "following_row_nodata": "• {label} · no listings seen in the last 90 days · followed {when}",
        "following_foot1": "New means first seen by us after you started following, not necessarily newly posted. Asking prices only.",
        "following_foot2": "The list is shared with the web Following page and everyone allowed to use this bot.",
        "btn_follow": "✅ Follow {label}",
        "btn_unfollow": "✖ Unfollow {label}",
        "btn_following": "👀 What I follow",
        # shortlist
        "save_done": "⭐ Saved to your shortlist: {label}",
        "save_exists": "{label} is already on your shortlist.",
        "save_gone": "I couldn't find that listing any more.",
        "save_not_enabled": "Saving cars isn't switched on in the database yet. Following models works; ask the owner to enable the shortlist.",
        "save_failed": "I couldn't save that right now. Please try again shortly.",
        "remove_done": "Removed from your shortlist: {label}",
        "remove_gone": "That car is no longer on your shortlist.",
        "remove_kept": "{label} has a status or note set on the web, so I left it on your shortlist. Remove it there.",
        "shortlist_empty": "Your shortlist is empty. Tap ⭐ under a result to save a car.",
        "shortlist_title": "Your shortlist",
        "shortlist_row": "• {label} · {price} · {status}",
        "shortlist_cut": "  ↓ price changed since you saved it: was {was}",
        "shortlist_rise": "  ↑ price changed since you saved it: was {was}",
        "shortlist_seen": "  {seen} · {url}",
        "shortlist_foot": "Statuses come from the web workspace. The list is shared with everyone allowed to use this bot. Asking prices only; availability is not verified.",
        "cand_shortlisted": "shortlisted",
        "cand_contacted": "contacted",
        "cand_inspect": "to inspect",
        "cand_passed": "passed",
        "btn_save": "⭐ {label}",
        "btn_remove": "✖ Remove from shortlist",
        "btn_shortlist": "⭐ My shortlist",
        "btn_show_listings": "🔎 Show listings",
    },
    "bs": {
        "unknown": "nepoznato",
        "price_unavailable": "cijena nije dostupna",
        "unparsed_model": "Neprepoznat model",
        "looking": "🔎 Tražim…",
        "chat_not_set_up": "Još nisam podešen da odgovaram u ovom četu (id četa: {chat_id}). Vlasnik treba dodati taj id u postavku TELEGRAM_CHAT_ID u Vercelu (odvojen zarezom od trenutnog) i ponovo deployati.",
        "db_unavailable": "Baza je trenutno nedostupna, pa ne mogu odgovoriti. Pokušaj ponovo za koji trenutak.",
        "status_none": "Car Analyzer još nema nijedan završen prolaz kolektora.",
        "status_title": "Car Analyzer · {quality}",
        "status_latest_attempt": "Zadnji pokušaj: {when}",
        "status_counts": "{obs} zapažanja · {new} prvi put viđeno kod nas · {errors} grešaka",
        "status_visits": "Posjete izvoru: {search} pretraga · {detail} detalja ({done} završeno)",
        "status_latest_obs": "Zadnje pohranjeno zapažanje: {when}",
        "status_clean": "Zadnji potpuno čist prolaz: {when}",
        "status_observed_attempt": "Zadnji pokušaj sa zapažanjima: {when} ({status}, {n} zapažanja)",
        "status_note1": "Svježina se odnosi na naša zapažanja, a ne na datum objave oglasa.",
        "status_note2": "Nestanak iz pretrage nije dokaz da je auto prodan.",
        "trends_title": "Zabilježene promjene tražene cijene · zadnjih {days} dana",
        "trends_sample": "Uzorak: {sample} oglasa viđeno; {priced} s upotrebljivom cijenom",
        "trends_transitions": "Zabilježene promjene: {drops} padova · {rises} rastova",
        "fresh_line": "Svježina: zapažanja do {when}",
        "trends_note": "Ovo je ograničen uzorak koji smo vidjeli, a ne indeks cijelog tržišta. Tražene cijene nisu prodajne cijene.",
        "trends_usage": "Koristi /trends DANA s brojem od 1 do 90.",
        "budget_usage": "Koristi /budget IZNOS [marka ili model], npr.: /budget 25000 Golf",
        "budget_title": "Nedavno viđeno do {amount}",
        "budget_showing": "Prikazujem {n} rezultata iz baze:",
        "budget_footer": "Ovo su viđeni oglasi, a ne preporuke za kupovinu niti provjerena dostupnost.",
        "observed_row": "  Viđeno {when} · {url}",
        "no_match_90": "Nema odgovarajućih oglasa u zadnjih 90 dana.",
        "compare_usage": "Koristi /compare MARKA MODEL | MARKA MODEL, npr.: /compare VW Golf | Škoda Octavia",
        "compare_title": "Poređenje iz baze · viđeno u zadnjih 90 dana",
        "cohort_sample": "Uzorak: {n} oglasa · {priced} s cijenom",
        "cohort_median": "Medijan tražene cijene: {median} · raspon {low}–{high}",
        "cohort_thin": "Mali uzorak: manje od 5 oglasa s cijenom, pa ove brojke uzmi s rezervom.",
        "compare_note": "Isti širok period od 90 dana; bez usklađivanja kilometraže i godišta. Tražene cijene nisu prodajne cijene.",
        "stats_title": "Tražene cijene · {label} · viđeno u zadnjih 90 dana",
        "stats_evidence": "Podaci iz baze",
        "stats_note": "Bez usklađivanja kilometraže i godišta. Tražene cijene nisu prodajne cijene.",
        "fresh_none": "Baza još nema pohranjenih zapažanja, pa nema na čemu da odgovorim.",
        "fresh_stale": "Zastarjeli podaci: najnovije pohranjeno zapažanje staro je {days} dana ({when}).",
        "fresh_run": "Zadnji pokušaj kolektora bio je {status}, pa pokrivenost možda nije potpuna.",
        "find_title": "Viđeni oglasi",
        "title_upto": "do {x}",
        "title_from": "od {x}",
        "no_more": "Nema više rezultata.",
        "search_ignored": "Nisam prepoznao „{term}“ kao marku ili model, pa tražim među svim modelima.",
        "find_hint": "Ako to nije marka ni model auta, pošalji /help da vidiš na šta mogu odgovoriti.",
        "find_showing": "Prikazujem {span} od {total} rezultata iz baze, prvo najnovije godište, zatim nedavno viđeni, pa najmanja kilometraža:",
        "seen_row": "Viđeno {age} ({when})",
        "old_warning": "⚠ Zadnji put viđeno prije više od {days} dana: oglas možda više nije aktivan. Provjeri link prije nego što se osloniš na njega.",
        "filters_note": "Oglasi bez upisane godine ili kilometraže nisu uključeni kad se koriste ti filteri.",
        "find_footer": "Ovo su viđene tražene cijene, a ne preporuke niti provjerena dostupnost.",
        "suggest_title": "Uža lista na osnovu podataka",
        "suggest_models_h": "Najviše oglasa u ovom budžetu (oglasi viđeni u zadnjih 90 dana):",
        "suggest_model_row": "{label} · {n} oglasa · medijan {price}{km} · najnovije {year}",
        "suggest_km": " · medijan {km} km",
        "suggest_notenough": "Nema dovoljno oglasa za poređenje modela (potrebno bar {n} po modelu).",
        "suggest_standouts_h": "Znatno jeftiniji od sličnih oglasa (isti model i godište, bar 15% ispod medijana, kilometraža jednaka ili manja od medijana, viđeni u zadnjih {days} dana):",
        "suggest_standout_row": "{label} · {year} · {price} ({pct}% ispod medijana od {med}, {n} sličnih) · {km} km",
        "suggest_none": "Trenutno nijedan svjež oglas ne odskače.",
        "suggest_foot1": "Najviše oglasa znači najviše oglasa u našoj bazi, a ne najprodavanije: nemamo podatke o prodaji.",
        "suggest_foot2": "Niska cijena u odnosu na slične oglase može značiti i oštećenje, brzu prodaju ili pogrešan unos. Provjeri svaki oglas. Ovo su viđene tražene cijene, a ne preporuke.",
        "chitchat": "Zdravo! Odgovaram na pitanja o polovnim autima iz naše olx.ba baze, npr. „Golf do 15000 KM“, „koliko košta Octavia“ ili „Golf vs Octavia“. Šta tražiš?",
        "clarify_suffix": "(Mogu samo pokazati šta je naša baza vidjela; ne mogu umjesto tebe birati auto.)",
        "clarify_currency": "Cijene u našoj bazi su tražene cijene u KM. Navedi iznos u KM, npr. „Golf do 15000 KM“.",
        "clarify_default": "Ne mogu umjesto tebe birati auto; mogu samo pokazati šta je naša baza vidjela.\n\nReci mi budžet, a ako želiš i model, godište ili ograničenje kilometraže. Na primjer:\n• Golf do 15000 KM\n• Octavia od 2016 do 150000 km\n• uporedi Golf i Octavia",
        "unsupported_forecast": "Ne predviđam cijene niti tržište. Mogu samo reći šta je naša baza vidjela, npr. „trend zadnjih 30 dana“ ili „koliko košta Golf“.",
        "unsupported_default": "To ne mogu povezati s nečim na šta naša baza može odgovoriti, pa neću nagađati.\n\nMogu odgovoriti na: stanje i svježinu podataka, najnovije oglase, promjene cijena (npr. „trend zadnjih 30 dana“), oglase do nekog budžeta („Golf do 15000 KM“), tražene cijene modela („koliko košta Octavia“) i poređenja („Golf vs Octavia“). Odgovori na neku od mojih poruka da je suziš.",
        "latest_none": "Nema prolaza kolektora sa zapažanjima.",
        "latest_title": "Zadnji prolaz sa zapažanjima · {status} · {when}",
        "latest_counts": "{obs} zapažanja · {new} ID-jeva prvi put viđeno kod nas · {errors} grešaka",
        "latest_few": "Nekoliko ID-jeva koje smo u ovom prolazu prvi put vidjeli:",
        "latest_no_ids": "Nijedan prvi put viđen ID nije povezan s ovim prolazom.",
        "latest_note": "Prvi put viđeno kod nas ne znači da je tek objavljeno na OLX-u.",
        "help": "Car Analyzer čita samo naše podatke iz Supabase baze.\n\n/status — stanje i svježina kolektora\n/latest — najnovija zapažanja i nekoliko oglasa\n/trends 7 — promjene cijena (1–90 dana)\n/budget 25000 Golf — nedavno viđeni oglasi\n/compare Volkswagen Golf | Škoda Octavia — šire poređenje iz baze\n/ask Golf do 15000 KM od 2016 — pitaj običnim jezikom, bosanski ili engleski\n/follow Golf — prati model\n/following — šta pratiš i šta je novo\n/shortlist — auta koja si sačuvao\n/help — ovaj vodič\n\nMožeš i da me @označiš ili odgovoriš na neku moju poruku pitanjem, npr. „Octavia do 20000 KM“, „koliko košta Golf“, „Golf vs Octavia“, ili suženjem poput „od 2018“. Pokrivenost baze je ograničena; prvo viđenje ne računam kao objavu, a nestanak iz pretrage kao prodaju.",
        "welcome": "Zdravo! Ja sam Garaža. Pitaj me za polovne aute iz naše olx.ba baze, na bosanskom ili engleskom. Dodirni primjer ili jednostavno piši.",
        "btn_status": "Stanje",
        "btn_latest": "Novo",
        "btn_trends7": "Trend 7 dana",
        "btn_trends30": "Trend 30 dana",
        "btn_market": "Otvori Market",
        "btn_more": "Još rezultata ({a}–{b})",
        "btn_show_first": "Pokaži oglase ({a}–{b})",
        "btn_cheaper": "Budžet −10%",
        "btn_pricier": "Budžet +10%",
        "btn_newer": "📅 Od {year}",
        "btn_lowkm": "🛣 Do {km} km",
        "btn_budget": "{amount} KM",
        "btn_ex_find": "🔎 Golf do 15000 KM",
        "btn_ex_suggest": "💡 Šta da kupim do 30000 KM",
        "btn_ex_latest": "🆕 Šta je novo",
        "follow_ask": "Koji model da pratim? Dodirni onaj koji misliš. Praćenje se vidi i na web stranici Following.",
        "follow_ask_one": "Da pratim {label}? Praćenje se vidi i na web stranici Following.",
        "follow_none": "Nisam našao model sličan „{query}“ u našim oglasima. Probaj marku i model, npr. „Volkswagen Golf“.",
        "follow_done": "✅ Sad pratim {label}. Napiši „šta pratim“ kad god želiš vidjeti šta je novo.",
        "follow_exists": "Već pratiš {label}.",
        "follow_failed": "Trenutno ne mogu to sačuvati. Pokušaj ponovo za koji trenutak.",
        "unfollow_done": "Prestao sam pratiti {label}.",
        "unfollow_gone": "Tog modela više nema na tvojoj listi.",
        "following_empty": "Još ne pratiš nijedan model. Napiši „prati Golf“ za početak.",
        "following_title": "Praćeni modeli",
        "following_row": "• {label} · trenutno {n} oglasa · medijan {median} · {new} prvi put viđeno otkad pratiš ({when})",
        "following_row_nodata": "• {label} · nijedan oglas viđen u zadnjih 90 dana · praćeno od {when}",
        "following_foot1": "Novo znači prvi put viđeno kod nas nakon što si počeo pratiti, ne nužno tek objavljeno. Samo tražene cijene.",
        "following_foot2": "Lista je zajednička sa web stranicom Following i svima koji smiju koristiti ovog bota.",
        "btn_follow": "✅ Prati {label}",
        "btn_unfollow": "✖ Prestani pratiti {label}",
        "btn_following": "👀 Šta pratim",
        "save_done": "⭐ Sačuvano na tvoju listu: {label}",
        "save_exists": "{label} je već na tvojoj listi.",
        "save_gone": "Taj oglas više ne mogu naći.",
        "save_not_enabled": "Čuvanje auta još nije uključeno u bazi. Praćenje modela radi; zamoli vlasnika da uključi listu.",
        "save_failed": "Trenutno ne mogu to sačuvati. Pokušaj ponovo za koji trenutak.",
        "remove_done": "Uklonjeno s tvoje liste: {label}",
        "remove_gone": "Tog auta više nema na tvojoj listi.",
        "remove_kept": "{label} ima status ili bilješku postavljenu na webu, pa sam ga ostavio na listi. Ukloni ga tamo.",
        "shortlist_empty": "Tvoja lista je prazna. Dodirni ⭐ ispod rezultata da sačuvaš auto.",
        "shortlist_title": "Tvoja lista",
        "shortlist_row": "• {label} · {price} · {status}",
        "shortlist_cut": "  ↓ cijena se promijenila otkad si ga sačuvao: bila {was}",
        "shortlist_rise": "  ↑ cijena se promijenila otkad si ga sačuvao: bila {was}",
        "shortlist_seen": "  {seen} · {url}",
        "shortlist_foot": "Statusi dolaze iz web aplikacije. Lista je zajednička sa svima koji smiju koristiti ovog bota. Samo tražene cijene; dostupnost nije provjerena.",
        "cand_shortlisted": "na listi",
        "cand_contacted": "kontaktirano",
        "cand_inspect": "za pregled",
        "cand_passed": "odbačeno",
        "btn_save": "⭐ {label}",
        "btn_remove": "✖ Ukloni s liste",
        "btn_shortlist": "⭐ Moja lista",
        "btn_show_listings": "🔎 Pokaži oglase",
    },
}

# Words that mark a message as Bosnian/Croatian/Serbian or English. Deliberately small and
# high-precision: an unknown language returns None and the caller falls back.
_BS_WORDS = set("""
je se da li hej ima imam imas trebam treba mi ti za do od sta šta koliko košta kosta cijena cijene cijenu
auto auta autom kupim kupiti kupovinu hvala molim bok zdravo pozdrav dobro dobar kako gdje gdje mogu hocu
hoću zelim želim trazim tražim nesto nešto jeftino jeftinije novije stariji uporedi usporedi ispod preko
budzet budžet godina godište kilometara kilometraža pokazi pokaži daj daj mi vise više manje najbolje
najpametnije prodaje prati pratim pratiti zaprati praćeni sačuvao sačuvani sacuvao sacuvani lista nova novo novi nove koji koja koje ovo ono moje tvoje je li jel sam smo ste su
""".split())
_EN_WORDS = set("""
the an is are was what which how much many show me find get buy want need looking for under below
above over with without and or but than please hello hi hey thanks thank you cars car price prices cheap
cheaper cheapest best deal deals budget year mileage latest new newest should can could would there have
has does my it this that of in from follow following track watch followed
""".split())


def detect_language(text_value):
    """Return 'bs', 'en' or None when the message does not say."""
    value = (text_value or "").lower()
    if not value.strip():
        return None
    tokens = re.findall(r"[^\W\d_]+", value)
    score_bs = sum(1 for token in tokens if token in _BS_WORDS)
    score_en = sum(1 for token in tokens if token in _EN_WORDS)
    if re.search(r"[šđčćž]", value):
        score_bs += 2
    if score_bs == score_en:
        return None
    return "bs" if score_bs > score_en else "en"


def telegram_language(code):
    """Map a Telegram client language_code to a bot language, or None."""
    code = (code or "").lower().split("-")[0]
    if code in {"bs", "hr", "sr", "sh"}:
        return "bs"
    return "en" if code else None


def t(lang, key, **values):
    table = STRINGS.get(lang) or STRINGS[DEFAULT_LANGUAGE]
    template = table.get(key)
    if template is None:
        template = STRINGS[DEFAULT_LANGUAGE][key]
    return template.format(**values) if values else template


def plural_bs(number, one, few, many):
    """Bosnian plural choice: 1 dan, 2-4 dana, 5+ dana (11-14 are 'many')."""
    n = abs(int(number))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def status_label(lang, status):
    status = str(status or "")
    if lang == "bs":
        return BS_STATUS.get(status.lower(), status.upper())
    return status.upper()
