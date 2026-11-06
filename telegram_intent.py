"""Deterministic question parsing for the private Telegram bot.

No model, network or database access lives here. A question is reduced to a small,
validated ``Intent`` that the bot maps onto existing read-only query helpers. Anything
that cannot be understood becomes an explicit "unsupported" or "clarify" intent
instead of a guess.

Follow-up context is never stored. It is recovered only from the bot message that
the user explicitly replied to, so it cannot cross users or chats.
"""

from dataclasses import dataclass, replace
import re
import unicodedata


MAX_QUESTION = 400
MAX_SEARCH = 40
PRICE_RANGE = (1_000, 1_000_000)
YEAR_RANGE = (1980, 2035)
MILEAGE_RANGE = (1_000, 1_000_000)

TITLE_FIND = "Observed listings"
TITLE_BUDGET = "Recently observed at or below "
TITLE_TRENDS = "Observed asking-price changes · last "


@dataclass(frozen=True)
class Filters:
    search: str = ""
    budget: int | None = None
    year_min: int | None = None
    mileage_max: int | None = None

    def is_empty(self):
        return not (self.search or self.budget or self.year_min or self.mileage_max)


@dataclass(frozen=True)
class Intent:
    kind: str  # status|latest|trends|find|compare|model_stats|clarify|unsupported
    filters: Filters = Filters()
    days: int = 7
    labels: tuple[str, ...] = ()
    note: str = ""
    question: str = ""
    offset: int = 0
    reply: str = ""


def fold(value):
    """Lower-case and strip diacritics while keeping string length unchanged."""
    out = []
    for char in (value or "").lower():
        if char == "đ":
            out.append("d")
            continue
        base = unicodedata.normalize("NFKD", char)
        out.append(base[0] if base else char)
    return "".join(out)


def clean_search(value):
    value = re.sub(r"[^\w\s.\-]|_", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()[:MAX_SEARCH].strip()


_AMOUNT = r"([0-9]{1,3}(?:[., ][0-9]{3})+(?![0-9])|[0-9]+)\s*(k(?![a-z0-9]))?"


def _amount(digits, thousands, bounds):
    cleaned = re.sub(r"[^0-9]", "", digits)
    if not cleaned:
        return None
    value = int(cleaned) * (1000 if thousands else 1)
    return value if bounds[0] <= value <= bounds[1] else None


_STOPWORDS = set("""
a an the any all some what which who how when where whats what's show me my find get buy want need
looking look for search can could would should i we you it is are was be there have has do does did
to of in on at with without and or but than then please give list tell about cars car vehicle
vehicles auto auta automobil automobili listing listings ad ads under below over above up max
maximum min minimum within around budget from since after newer older year years km bam kam marks
mark euro eur price priced cheap cheaper cost costs much many best good great deal deals ima li
daj mi pokazi trazim trebam zelim kupiti kupim koji koja koje za do ispod preko od iza nakon
novije stariji starije oko cijena cijene cijenu koliko kosta koliko-kosta budzet manje vise sta
ili ima-li da u na sa bez s the new newest latest recent recently or less more
days day dana dan weeks week sedmica sedmicu mjesec month last past zadnjih posljednjih
bosnia bih right now something anything one status fresh freshness stale collector interesting finds update updated today data how old trend trends
recommend recommendation recommendations preporuci preporuka preporuke preporucite worth deal besides novo najnovije
suggest suggestion imam najbolje najbolji najbolja najpametnije najpametniji pametno smart smartest sells selling sell
prodaje prodaju prodavati prodaje-se najtrazenije najtrazeniji popular worthwhile savjet advice
value follow following followed track watch watching prati pratiti pratim zaprati pracenje praceni am im ive whats
shortlist shortlisted saved sacuvao sacuvani sacuvan sam lista auti spisak
moj moja moje my nesto family porodica porodicu porodicni porodicno familiju sporty
""".split())

_MENTION = re.compile(r"@\w+")
_MILEAGE_CUES = r"(?:mileage|kilometraz\w*|kilometers|kilometres|odvozen\w*|prijeden\w*|on the clock)"


def strip_mentions(value):
    return re.sub(r"\s+", " ", _MENTION.sub(" ", value or "")).strip()


def _extract_filters(folded, original):
    """Return Filters plus the original text with recognised spans removed."""
    mask = [True] * len(original)

    def consume(match):
        for index in range(match.start(), match.end()):
            mask[index] = False

    year = None
    for pattern in (
        r"(?:from|since|after|newer than|od|iza|nakon|novije od)\s*((?:19|20)[0-9]{2})\b",
        r"\b((?:19|20)[0-9]{2})\s*(?:\+|or newer|or later|and newer|ili novije|i novije)",
        r"\b((?:19|20)[0-9]{2})\b",
    ):
        match = re.search(pattern, folded)
        if match:
            candidate = int(match.group(1))
            if YEAR_RANGE[0] <= candidate <= YEAR_RANGE[1]:
                year = candidate
                consume(match)
                break

    mileage = None
    for pattern in (
        rf"{_AMOUNT}\s*(?:km|kilometara)?\s*(?:{_MILEAGE_CUES}|or less|max)\b",
        rf"{_MILEAGE_CUES}\D{{0,16}}?{_AMOUNT}\s*(?:km|kilometara)?",
    ):
        match = re.search(pattern, folded)
        if match:
            candidate = _amount(match.group(1), match.group(2), MILEAGE_RANGE)
            if candidate:
                mileage = candidate
                consume(match)
                break

    budget = None
    spans = [m for m in re.finditer(rf"(?<![0-9]){_AMOUNT}\s*(?:km|bam|kam|maraka|marks|eur|euro)?", folded)
             if all(mask[i] for i in range(m.start(), m.end()))]
    for match in spans:
        candidate = _amount(match.group(1), match.group(2), PRICE_RANGE)
        if candidate:
            budget = candidate
            consume(match)
            break

    rest = "".join(char if keep else " " for char, keep in zip(original, mask))
    return Filters(budget=budget, year_min=year, mileage_max=mileage), rest


def _singular(token):
    """Drop a plural ending so 'golfs' / 'golfovi' search as 'golf' (LIKE is substring-based)."""
    lowered = token.lower()
    if len(lowered) > 5 and lowered.endswith(("ovi", "evi")):
        return token[:-3]
    if len(lowered) > 4 and lowered.endswith("s") and not lowered.endswith(("ss", "es", "us", "is")):
        return token[:-1]
    return token


def _search_tokens(rest):
    tokens = []
    for raw in re.split(r"\s+", rest.lower().replace("'", "").replace("\u2019", "")):
        token = clean_search(raw)
        if token and fold(token) not in _STOPWORDS and fold(token.rstrip("s")) not in _STOPWORDS:
            tokens.append(_singular(token))
    return tokens


def _search_text(rest):
    return clean_search(" ".join(_search_tokens(rest)))


_COMPARE_SPLIT = re.compile(
    r"\s*(?:\||\bvs\.?\b|\bversus\b|\bnaspram\b|\bagainst\b|\bcompared to\b|\bor\b|\bili\b|\band\b|\bi\b|\bwith\b)\s*",
    re.I)


def _compare_labels(text_value):
    folded = fold(text_value)
    match = re.search(r"\b(?:compare|comparison|uporedi|poredi|usporedi|razlika izmedu)\b(.*)", folded)
    candidates = []
    if match:
        body = text_value[match.start(1):]
        candidates = _COMPARE_SPLIT.split(body)
    elif re.search(r"\bvs\.?\b|\bversus\b|\bnaspram\b|\|", folded):
        candidates = _COMPARE_SPLIT.split(text_value)
    elif re.search(r"\b(?:better|bolji|bolje|which)\b.*\b(?:or|ili)\b", folded):
        candidates = _COMPARE_SPLIT.split(text_value)
    labels = []
    for chunk in candidates:
        label = _search_text(chunk)
        if label:
            labels.append(label)
    return tuple(labels[:2]) if len(labels) == 2 else ()


def _days_from(folded):
    match = re.search(r"\b([1-9][0-9]?)\s*(?:d\b|day|days|dan|dana)", folded)
    if not match:
        match = re.search(r"\b(?:last|past|zadnjih|posljednjih)\s*([1-9][0-9]?)\b", folded)
    if match:
        return max(1, min(int(match.group(1)), 90))
    if re.search(r"\b(?:month|mjesec)\b", folded):
        return 30
    if re.search(r"\b(?:week|sedmic|sedmica)\w*", folded):
        return 7
    return 7


_FOLLOWUP_CUE = re.compile(
    r"^(?:and|also|what about|how about|and what about|a sta je sa|sta sa|a|i|only|just)\b", re.I)


def looks_like_followup(text_value):
    """True when a message reads as a refinement of the previous answer.

    Used only for the best-effort memory of a private chat: filter-only messages
    ("from 2018", "under 12000") or ones opening with "what about ...".
    """
    original = strip_mentions((text_value or "")[:MAX_QUESTION]).strip()
    if _FOLLOWUP_CUE.match(original):
        return True
    filters, rest = _extract_filters(fold(original), original)
    return not filters.is_empty() and not _search_tokens(rest)


# Titles the bot puts on its first line, per language. Replies are parsed back from them, so a
# follow-up works whichever language the answer was in.
_TITLES = (
    {"trends": "Observed asking-price changes · last ", "trends_unit": "days",
     "budget": "Recently observed at or below ", "find": "Observed listings", "upto": "up to ", "from": "from "},
    {"trends": "Zabilježene promjene tražene cijene · zadnjih ", "trends_unit": "dana",
     "budget": "Nedavno viđeno do ", "find": "Viđeni oglasi", "upto": "do ", "from": "od "},
)


def context_from_reply(reply_text):
    """Recover the previous answer's scope from the bot message being replied to."""
    first = (reply_text or "").strip().split("\n", 1)[0]
    for titles in _TITLES:
        if first.startswith(titles["trends"]):
            match = re.match(rf"([0-9]{{1,2}}) {titles['trends_unit']}", first[len(titles["trends"]):])
            if match:
                return Intent("trends", days=max(1, min(int(match.group(1)), 90)))
            return None
        if first.startswith(titles["budget"]):
            body = first[len(titles["budget"]):]
            amount_part, _, search = body.partition(" · ")
            budget = _amount(re.sub(r"\s*KM$", "", amount_part), None, PRICE_RANGE)
            if budget:
                return Intent("find", Filters(clean_search(search), budget))
            return None
        if first.startswith(titles["find"]):
            search, budget, year, mileage = "", None, None, None
            upto, since = titles["upto"], titles["from"]
            for piece in first[len(titles["find"]):].split(" · "):
                piece = piece.strip()
                if not piece:
                    continue
                if piece.startswith(upto) and piece.endswith(" KM"):
                    budget = _amount(piece[len(upto):-3], None, PRICE_RANGE)
                elif piece.startswith(upto) and piece.endswith(" km"):
                    mileage = _amount(piece[len(upto):-3], None, MILEAGE_RANGE)
                elif piece.startswith(since) and piece[len(since):].strip().isdigit():
                    year = _amount(piece[len(since):], None, YEAR_RANGE)
                else:
                    search = clean_search(piece)
            return Intent("find", Filters(search, budget, year, mileage))
    return None


_FOLLOW = r"\b(?:follow\w*|track\w*|watch\w*|prati\w*|zaprati\w*|pracenj\w*|praceni\w*)\b"
_SHORTLIST = r"\b(?:shortlist\w*|saved (?:cars?|ads?|listings?)|sacuvan\w*|sacuva\w*|moja lista|moj spisak|my list)\b"
_STATUS = r"\b(?:status|fresh\w*|up to date|stale|collector|how old|when was|azurn\w*|kolektor)\b"
_LATEST = r"\b(?:latest|newest|what'?s new|whats new|new listings|interesting|finds|najnovij\w*|sta ima novo|novo)\b"
_TRENDS = (r"\b(?:trend\w*|price changes?|prices? (?:drop|fall|rise|change)\w*|pojeftin\w*|poskup\w*)|"
           r"\bcijen\w*.*\b(?:mijenj|pad|rast)\w*|\b(?:mijenj|pad|rast)\w*.*\bcijen\w*")
_FORECAST = r"\b(?:will|predict\w*|forecast\w*|going to|next (?:year|month|week)|prognoz\w*|hoce li|budu[cć]\w*)\b"
_CURRENCY = r"\b(?:eur|euro|euros|usd|dollars?)\b|[€$]"
_ACK = r"^(?:thanks|thank you|thx|ty|ok|okay|hvala|super|great|nice|cool|sjajno)\W*$"
_SUGGEST = (r"\b(?:suggest\w*|recommend\w*|preporuc\w*|should i buy|what to buy|what (?:car|cars) (?:should|to)|"
            r"which car|best (?:buy|value|deal|car)s?|worth buying|smartest|najpametnij\w*|pametno\w*|"
            r"sta (?:da )?kupi\w*|sta kupiti|koji auto|najbolje (?:se )?prodaj\w*|najtrazenij\w*|"
            r"best sell\w*|what sells|sells? best|popular)\b")
_ADVICE = r"\b(?:should i buy|what (?:car|to buy)|which car|recommend\w*|best (?:car|deal)s?|worth buying|preporuc\w*|koji auto|sta kupiti|sta da kupim)\b"
_STATS = r"\b(?:how much|average|typical|median|cost\w*|price of|koliko|prosjecn\w*|cijena)\b"


def parse_question(text_value, context=None):
    """Turn one question into an Intent. ``context`` is an Intent from a replied-to answer."""
    original = strip_mentions((text_value or "")[:MAX_QUESTION]).strip()
    original = re.sub(r"^/ask(?:@\w+)?\s*", "", original, flags=re.I).strip()
    if not original:
        return Intent("unsupported")
    folded = fold(original)

    if re.search(_ACK, folded):
        return Intent("ignore")
    if re.search(_FORECAST, folded):
        return Intent("unsupported", note="forecast")
    if re.search(_CURRENCY, folded):
        return Intent("clarify", note="currency")

    labels = _compare_labels(original)
    if labels:
        return Intent("compare", labels=labels)

    if re.search(_TRENDS, folded):
        days = _days_from(folded)
        if days == 7 and context and context.kind == "trends" and not re.search(r"\b(?:week|sedmic)", folded):
            return Intent("trends", days=context.days)
        return Intent("trends", days=days)

    filters, rest = _extract_filters(folded, original)
    tokens = _search_tokens(rest)
    named = tokens if any(not token.isdigit() for token in tokens) else []
    filters = replace(filters, search=clean_search(" ".join(named)))

    if re.search(_SHORTLIST, folded) and not filters.search and filters.budget is None:
        return Intent("shortlist")

    if re.search(_FOLLOW, folded):
        generic = {"model", "modeli", "modela", "models", "cars", "auta", "automobili"}
        named = " ".join(word for word in filters.search.split() if fold(word) not in generic)
        if named and filters.budget is None and filters.year_min is None and filters.mileage_max is None:
            return Intent("follow", Filters(search=named))
        return Intent("following")

    model_words = [t for t in tokens if len(t) > 2 or any(c.isdigit() for c in t)]
    if filters.budget and re.search(_SUGGEST, folded) and (not model_words or len(model_words) >= 3):
        return Intent("suggest", Filters(budget=filters.budget, year_min=filters.year_min,
                                          mileage_max=filters.mileage_max))

    if filters.is_empty():
        if re.search(_ADVICE, folded):
            return Intent("clarify")
        if re.search(_STATUS, folded):
            return Intent("status")
        if re.search(_LATEST, folded):
            return Intent("latest")

    if context and context.kind == "trends" and tokens and all(t.isdigit() for t in tokens):
        return Intent("trends", days=max(1, min(int(tokens[0]), 90)))

    if len(tokens) > 4:
        return Intent("unsupported")

    if context and context.kind == "find" and not filters.is_empty():
        return Intent("find", Filters(
            search=filters.search or context.filters.search,
            budget=filters.budget or context.filters.budget,
            year_min=filters.year_min or context.filters.year_min,
            mileage_max=filters.mileage_max or context.filters.mileage_max,
        ))

    if not filters.is_empty():
        if (filters.search and filters.budget is None and filters.year_min is None
                and filters.mileage_max is None and re.search(_STATS, folded)):
            return Intent("model_stats", filters)
        return Intent("find", filters)

    return Intent("unsupported")
