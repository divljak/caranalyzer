"""Deterministic question parsing for the private Telegram bot.

No model, network or database access lives here. A question is reduced to a small,
validated ``Intent`` that the bot maps onto existing read-only query helpers. Anything
that cannot be understood becomes an explicit "unsupported" or "clarify" intent
instead of a guess.

Follow-up context is never stored. It is recovered only from the bot message that
the user explicitly replied to, so it cannot cross users or chats.
"""

from dataclasses import dataclass, replace
from datetime import date
import difflib
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
    family: bool = False  # "za porodicu": limit to roomier models (see telegram_bot.FAMILY_MODELS)
    include_old: bool = False  # also show listings last seen 14-90 days ago (hidden by default)
    price_min: int | None = None  # lower price bound in KM ("od 50 - 60k"); ``budget`` is the upper one
    year_max: int | None = None  # upper model-year bound ("2022 - 2024")
    fuel: str = ""  # diesel|petrol|hybrid|electric|lpg ("dizel", "benzin", ...)
    transmission: str = ""  # automatic|manual ("automatik", "manuelni"); recorded on few listings
    new_ok: bool = False  # the user asks for new cars, so current-year cars under 1.000 km stay in

    @property
    def price_max(self):
        """The upper price bound. Stored as ``budget`` because every older button and answer uses that name."""
        return self.budget

    def numeric(self):
        """True when any price, year or mileage bound is set."""
        return bool(self.budget or self.price_min or self.year_min or self.year_max or self.mileage_max)

    def attributes(self):
        """True when a fuel or gearbox filter is set."""
        return bool(self.fuel or self.transmission)

    def is_empty(self):
        return not (self.search or self.numeric() or self.family or self.attributes())


def merge_filters(base, new):
    """Apply a follow-up's filters on top of the previous ones.

    A bound the follow-up states replaces the old one; the opposite bound is kept only while it is
    still consistent ("od 2020" after "2022-2024" keeps 2024; "do 40k" after "50-60k" drops 50k).
    Anything the follow-up does not mention (search, mileage, family, fuel, gearbox ...) is kept, so
    "ne, nego dizel" or "a automatik?" narrow the previous question instead of replacing the model.
    """
    def bounds(old_low, old_high, low, high):
        if low is None and high is None:
            return old_low, old_high
        if low is None and old_low is not None and (high is None or old_low <= high):
            low = old_low
        if high is None and old_high is not None and (low is None or old_high >= low):
            high = old_high
        return low, high

    price_min, budget = bounds(base.price_min, base.budget, new.price_min, new.budget)
    year_min, year_max = bounds(base.year_min, base.year_max, new.year_min, new.year_max)
    return Filters(
        search=new.search or base.search,
        budget=budget,
        year_min=year_min,
        mileage_max=new.mileage_max or base.mileage_max,
        family=new.family or base.family,
        include_old=new.include_old or base.include_old,
        price_min=price_min,
        year_max=year_max,
        fuel=new.fuel or base.fuel,
        transmission=new.transmission or base.transmission,
        new_ok=new.new_ok or base.new_ok,
    )


@dataclass(frozen=True)
class Intent:
    kind: str  # status|latest|trends|find|compare|model_stats|price_check|liquidity|clarify|unsupported
    filters: Filters = Filters()
    days: int = 7
    labels: tuple[str, ...] = ()
    note: str = ""
    question: str = ""
    offset: int = 0
    reply: str = ""
    # price_check only: the asking price, model year and mileage the user is asking about
    price: int | None = None
    year: int | None = None
    mileage: int | None = None


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


_AMOUNT = (r"([0-9]{1,3}(?:[., ][0-9]{3})+(?![0-9])|[0-9]+)\s*"
           r"(k(?![a-z0-9])|h?iljad\w*|tisuc\w*|thousand\w*)?")


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
je jel jeste jesi jesu li bi cu ces ce cemo cete ja mene me ti te vi vas vam nam nas on ona ono oni to ta taj ova ovaj ove
kakav kakva kakvo neki neka neko nesto svi sve sav dosta dovoljno malo mnogo vrlo bas samo jos vec onda pa ali jer ako dok
mogu mozes moze mozemo mozete mogao mogla htio htjela zelio zelim volio bih bi nadji nadi nades nadjes nadjem pronadji pronadi naci vidi vidjeti
reci kazi pitam pitao hocu hoces dobar dobra dobro dobre bolji bolja bolje fin fina fino lijep lijepa cao cao-cao ej hej
hi hey hello thx yes no yeah ok okay maybe also just really very quite enough something anything
moj moja moje my nesto family porodica porodicu porodicni porodicno familiju sporty
ne nije se ovo ono ovu gore probaj probati probaci probajte pogledaj this above try mileage kilometraza kilometraze
kilometers kilometres najlakse najbrze prodati prodam lako brzo staro stari staru godina godine godinu
godiste godista godistem izmedu izmedju between hiljada hiljade hiljadu tisuca tisuce thousand
mislim mislio mislila stavio stavila stavili napisao napisala rekao rekla trazio trazila nisam nego
pogresno krivo meant mean said asked wrote wanted wrong
normal normalna normalnu normalno normalan normalne realna realnu realno realan realne fer fair reasonable
typical average avg prosjek prosjeku prosjecno prosjecna prosjecnu prosjecan prosjecne cheapest
najjeftiniji najjeftinija najjeftinije najjeftinijeg najjeftinijih jeftin jeftina jeftino jeftini jeftinije
expensive skup skupa skupo skupi kosta kostaju kosti kostao vrijedi vrijede worth treba trebao trebala trebalo
isto same that thats generacija generacije generation gen model modela modeli verzija iljada iljade
is isn its it's price prices good fine okej ovog ovom taj tog
ponuda ponude ponudu ponudi offer offers ove ovih sedmice sedmici tjedna tjedan
""".split())

# Words that start a question or describe a price, never part of a make or model ("prosječno",
# "najjeftiniji", "normalna"): dropped from the search text whatever their ending.
_FILLER_PREFIXES = ("prosjec", "najjeftin", "jeftin", "normaln", "realn", "najpovoljn", "povoljn")

_MENTION = re.compile(r"@\w+")
_MILEAGE_CUES = r"(?:mileage|kilometraz\w*|kilometers|kilometres|odvozen\w*|prijeden\w*|on the clock)"


def strip_mentions(value):
    return re.sub(r"\s+", " ", _MENTION.sub(" ", value or "")).strip()


_FAMILY = (r"\b(?:porodic\w*|familij\w*|famil(?:y|ies)|djec\w*|dijete|djete|kids?|children|karavan\w*|"
           r"monovolumen\w*|kombi\w*|wagon|estate|mpv|minivan|prostran\w*|roomy|spacious|sedmosjed\w*|7 sjedist\w*)\b")
_INCLUDE_OLD = (r"\b(?:(?:i |ukljuci (?:i )?|pokazi (?:i )?|sa |s )starij\w*|"
                r"include (?:the )?older|older (?:ones|ads|listings) too|with older|incl\w* old\w*)\b")


_FUELS = (
    ("diesel", r"\b(?:dizel\w*|diesel\w*|dizl\w*|tdi|tdci|hdi|dci|crdi|cdi|jtd\w*|multijet)\b"),
    ("petrol", r"\b(?:benzin\w*|petrol|gasoline|tsi|tfsi)\b"),
    ("hybrid", r"\b(?:hibrid\w*|hybrid\w*|phev)\b"),
    ("electric", r"\b(?:elektri\w*|elektro\w*|electric\w*)\b"),
    ("lpg", r"\b(?:lpg|plin\w*|autoplin\w*|autogas)\b"),
)
_GEARBOXES = (
    ("automatic", r"\b(?:automat(?:ik\w*|ic\w*|sk\w*|u|a)?|dsg|tiptronic|steptronic)\b"),
    ("manual", r"\b(?:manuel\w*|manual\w*|rucn\w*|mehanik\w*)\b"),
)
# "novi auto", "brand new", "0 km": the user wants new cars, so dealer cars at 0 km are not hidden.
_NEW_CARS = (r"\b(?:nov[oiau]?|nove|new|brand new)\s+(?:auto\w*|car\w*|vozil\w*|kola|automobil\w*)\b|"
             r"\bbrand new\b|\b0\s*km\b|\bnula kilometar\w*|\bfabricki nov\w*|\bnove? iz salona\b")


def _attribute(folded, table):
    """(value, matches) for the single fuel or gearbox a message names; ('', ()) when none or conflicting."""
    found = [(value, match) for value, pattern in table for match in re.finditer(pattern, folded)]
    values = {value for value, _ in found}
    if len(values) != 1:
        return "", tuple(match for _, match in found) if len(values) > 1 else ()
    return values.pop(), tuple(match for _, match in found)


def wants_family(folded):
    return re.search(_FAMILY, folded) is not None


def wants_older(folded):
    return re.search(_INCLUDE_OLD, folded) is not None


_RANGE_FROM = r"(?:(?P<lead>od|from|izmedj?u|between)\s*)?"
_RANGE_SEP = r"\s*(?P<sep>-|–|—|do|to|until|i|and)\s*"
_YEAR_SPAN = re.compile(
    _RANGE_FROM + r"\b(?P<a>(?:19|20)[0-9]{2})" + _RANGE_SEP + r"(?P<b>(?:19|20)[0-9]{2})\b"
    r"(?!\s*(?:k(?![a-z])|km|bam|kam|marak\w*|hiljad\w*|tisuc\w*|thousand|eur\w*|[.,]?000))")
_THOUSANDS = r"k(?![a-z0-9])|h?iljad\w*|tisuc\w*|thousand"
# "preko 40k", "iznad 30.000 KM", "over 20k": a lower price bound only.
_ABOVE = r"\b(?:preko|iznad|vise od|vece od|over|above|more than|at least|najmanje|minimalno|minimum)\s*"
_PRICE_UNIT = r"km|bam|kam|maraka|marke|marks?"
_PRICE_SPAN = re.compile(
    r"(?<![0-9.,])" + _RANGE_FROM
    + r"(?P<a>[0-9]{1,3}(?:[., ][0-9]{3})+(?![0-9])|[0-9]+)\s*(?P<ma>" + _THOUSANDS + r")?\s*(?P<ua>" + _PRICE_UNIT
    + r")?(?![a-z0-9])" + _RANGE_SEP
    + r"(?P<b>[0-9]{1,3}(?:[., ][0-9]{3})+(?![0-9])|[0-9]+)\s*(?P<mb>" + _THOUSANDS + r")?\s*(?P<ub>" + _PRICE_UNIT
    + r")?(?![a-z0-9])")


def _year_range(folded):
    """('2022 - 2024' | 'od 2022 do 2024' | 'izmedu 2018 i 2020') -> (match, low, high) or None."""
    for match in _YEAR_SPAN.finditer(folded):
        if match.group("sep") in {"i", "and"} and not match.group("lead"):
            continue
        low, high = sorted((int(match.group("a")), int(match.group("b"))))
        if YEAR_RANGE[0] <= low <= high <= YEAR_RANGE[1] and low < high:
            return match, low, high
    return None


def _price_range(folded, original, km_is_price):
    """A two-sided amount: ('od 50 - 60k' | '50.000 do 60.000 KM' | 'izmedu 50 i 60 hiljada').

    Returns (match, kind, low, high) where kind is "price" or "mileage", or None. A shared "k" /
    "hiljada" applies to both ends ("50-60k" is 50.000-60.000). Lower-case "km" means mileage
    unless ``km_is_price`` (a correction to a price question); "Km" is left to the one-tap question.
    """
    for match in _PRICE_SPAN.finditer(folded):
        if match.group("sep") in {"i", "and"} and not match.group("lead"):
            continue
        units = [original[match.start(g):match.end(g)] for g in ("ua", "ub") if match.group(g)]
        if len(units) == 2 and units[0] != units[1]:
            continue  # "12000 KM do 200000 km": a price and a mileage, not one range
        many_a, many_b = bool(match.group("ma")), bool(match.group("mb"))
        low_digits = re.sub(r"[^0-9]", "", match.group("a"))
        high_digits = re.sub(r"[^0-9]", "", match.group("b"))
        if not low_digits or not high_digits:
            continue
        low, high = int(low_digits), int(high_digits)
        if many_a or (many_b and low < 1000):
            low *= 1000
        if many_b or (many_a and high < 1000):
            high *= 1000
        if "km" in units and not km_is_price:
            kind, bounds = "mileage", MILEAGE_RANGE
        elif any(unit in {"Km", "kM"} for unit in units) and not km_is_price:
            continue
        else:
            kind, bounds = "price", PRICE_RANGE
        low, high = sorted((low, high))
        if bounds[0] <= low < high <= bounds[1]:
            return match, kind, low, high
    return None


def _extract_filters(folded, original, km_is_price=False):
    """Return Filters plus the original text with recognised spans removed.

    ``km_is_price``: read a lower-case "km" after an amount as a price (KM). Only set when the
    message corrects a price question and says nothing about mileage.
    """
    mask = [True] * len(original)

    def consume(match):
        for index in range(match.start(), match.end()):
            mask[index] = False

    year = year_max = None
    span = _year_range(folded)
    if span:
        consume(span[0])
        year, year_max = span[1], span[2]
    for pattern in (() if span else (
        r"(?:from|since|after|newer than|od|iza|nakon|novije od)\s*((?:19|20)[0-9]{2})\b",
        r"\b((?:19|20)[0-9]{2})\s*(?:\+|or newer|or later|and newer|ili novije|i novije)",
        r"\b((?:19|20)[0-9]{2})\b",
    )):
        match = re.search(pattern, folded)
        if match:
            candidate = int(match.group(1))
            if YEAR_RANGE[0] <= candidate <= YEAR_RANGE[1]:
                year = candidate
                consume(match)
                break

    if year is None:
        # "staro do 10 godina", "ne starije od 8 godina", "do 10 godina staro", "up to 10 years old"
        for pattern in (
            r"\b(?:star\w*|mladj?\w*|old)\s+(?:do|od|max|up to|under|less than|than)?\s*([0-9]{1,2})\s*(?:god\w*|years?)\b",
            r"\b(?:do|max|up to|under|less than)\s*([0-9]{1,2})\s*(?:god\w*|years?)(?:\s*(?:star\w*|old))?\b",
            r"\b([0-9]{1,2})\s*(?:god\w*|years?)\s*(?:star\w*|old)\b",
        ):
            match = re.search(pattern, folded)
            if match and 1 <= int(match.group(1)) <= 40:
                year = date.today().year - int(match.group(1))
                consume(match)
                break

    mileage = None
    budget = price_min = None
    unit_unclear = None
    price_span = _price_range(folded, original, km_is_price)
    if price_span and all(mask[i] for i in range(price_span[0].start(), price_span[0].end())):
        consume(price_span[0])
        if price_span[1] == "price":
            price_min, budget = price_span[2], price_span[3]
        else:
            mileage = price_span[3]
    # A number followed by "km" written in lowercase is mileage ("do 20000km"); "KM", "maraka" or
    # "BAM" is a price. A mixed-case "Km" could be either, so the bot asks instead of guessing.
    # When correcting a price question (``km_is_price``) a lower-case "km" is read as KM too.
    for match in re.finditer(rf"(?<![0-9.,]){_AMOUNT}\s*(km|Km|kM)\b", original):
        if mileage is not None or km_is_price:
            break
        if not all(mask[i] for i in range(match.start(), match.end())):
            continue
        if match.group(3) == "km":
            candidate = _amount(match.group(1), match.group(2), MILEAGE_RANGE)
            if candidate:
                mileage = candidate
                consume(match)
                break
        else:
            unit_unclear = _amount(match.group(1), match.group(2), MILEAGE_RANGE)
            consume(match)
            break
    for pattern in (
        rf"{_AMOUNT}\s*(?:km|kilometara)?\s*(?:{_MILEAGE_CUES}|or less|max)\b",
        rf"{_MILEAGE_CUES}\D{{0,16}}?{_AMOUNT}\s*(?:km|kilometara)?",
    ):
        if mileage is not None:
            break
        match = re.search(pattern, folded)
        if match and all(mask[i] for i in range(match.start(1), match.end(1))):
            candidate = _amount(match.group(1), match.group(2), MILEAGE_RANGE)
            if candidate:
                mileage = candidate
                consume(match)
                break

    if price_min is None and budget is None:
        # "preko 40k" / "iznad 30.000 KM" / "over 20k": only a lower bound. A lower-case "km" after the
        # amount is mileage, which has no lower bound here, so it is left alone.
        for match in re.finditer(rf"{_ABOVE}(?<![0-9]){_AMOUNT}\s*(km|bam|kam|maraka|marke|marks?|eur\w*)?(?![a-z])",
                                 folded):
            if not all(mask[i] for i in range(match.start(), match.end())):
                continue
            if match.group(3) and original[match.start(3):match.end(3)] == "km" and not km_is_price:
                continue
            candidate = _amount(match.group(1), match.group(2), PRICE_RANGE)
            if candidate:
                price_min = candidate
                consume(match)
                break

    spans = [m for m in re.finditer(rf"(?<![0-9]){_AMOUNT}\s*(?:km|bam|kam|maraka|marks|eur|euro)?", folded)
             if all(mask[i] for i in range(m.start(), m.end()))]
    for match in spans:
        if budget is not None:
            break
        candidate = _amount(match.group(1), match.group(2), PRICE_RANGE)
        if candidate:
            budget = candidate
            consume(match)
            break

    fuel, fuel_spans = _attribute(folded, _FUELS)
    gearbox, gearbox_spans = _attribute(folded, _GEARBOXES)
    for match in (list(re.finditer(_FAMILY, folded)) + list(re.finditer(_INCLUDE_OLD, folded))
                  + list(re.finditer(_LIQUID, folded)) + list(re.finditer(_NEW_CARS, folded))
                  + list(fuel_spans) + list(gearbox_spans)):
        consume(match)
    rest = "".join(char if keep else " " for char, keep in zip(original, mask))
    filters = Filters(budget=budget, year_min=year, mileage_max=mileage, family=wants_family(folded),
                      include_old=wants_older(folded), price_min=price_min, year_max=year_max,
                      fuel=fuel, transmission=gearbox, new_ok=re.search(_NEW_CARS, folded) is not None)
    _UNCLEAR[0] = unit_unclear
    return filters, rest


_UNCLEAR = [None]  # amount whose unit (KM price or km mileage) was unclear in the last parse


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
        token = clean_search(raw).strip(".-")
        folded = fold(token)
        if (token and folded not in _STOPWORDS and fold(token.rstrip("s")) not in _STOPWORDS
                and not folded.startswith(_FILLER_PREFIXES)):
            tokens.append(_singular(token))
    return tokens


# Model generations people name by number ("Golf 7", "Octavia 3", "Passat B8"): searched as the model
# within the generation's model years, because the listings store "Volkswagen Golf", not "Golf 7".
# An open end is None. Approximate production years, slightly generous at the edges.
GENERATIONS = {
    "golf": {"3": (1991, 1998), "4": (1997, 2004), "5": (2003, 2009), "6": (2008, 2013), "7": (2012, 2020),
             "8": (2019, None)},
    "octavia": {"1": (1996, 2010), "2": (2004, 2013), "3": (2012, 2020), "4": (2019, None)},
    "passat": {"5": (1996, 2005), "6": (2005, 2011), "7": (2010, 2015), "8": (2014, None),
               "b5": (1996, 2005), "b6": (2005, 2011), "b7": (2010, 2015), "b8": (2014, None)},
    "polo": {"4": (2001, 2009), "5": (2009, 2017), "6": (2017, None)},
    "a4": {"b6": (2000, 2005), "b7": (2004, 2008), "b8": (2007, 2016), "b9": (2015, None)},
    "a6": {"c5": (1997, 2005), "c6": (2004, 2011), "c7": (2011, 2018), "c8": (2018, None)},
    "astra": {"g": (1998, 2005), "h": (2004, 2010), "j": (2009, 2016), "k": (2015, 2022)},
}


def _generation_model(token):
    """The GENERATIONS key a token names, allowing a small typo ("golff")."""
    folded = fold(token)
    if folded in GENERATIONS:
        return folded
    if len(folded) >= 4:
        close = difflib.get_close_matches(folded, list(GENERATIONS), n=1, cutoff=0.8)
        if close:
            return close[0]
    return None


def generation_hint(tokens):
    """("golf", "7") -> (["golf"], (2012, 2020)); tokens unchanged and None when no generation is named.

    Accepts "golf 7", "golf7", "golf vii" is not handled. A typo in the model ("golff 7") is repaired
    only when a generation number follows it, so the pair is unmistakable."""
    tokens = list(tokens)
    for index, token in enumerate(tokens):
        joined = re.fullmatch(r"([a-z]+)([0-9])", fold(token))
        if joined and _generation_model(joined.group(1)):
            model = _generation_model(joined.group(1))
            span = GENERATIONS[model].get(joined.group(2))
            if span:
                return tokens[:index] + [model] + tokens[index + 1:], span
        model = _generation_model(token)
        if model and index + 1 < len(tokens):
            span = GENERATIONS[model].get(fold(tokens[index + 1]))
            if span:
                return tokens[:index] + [model] + tokens[index + 2:], span
    return tokens, None


def model_hint(label):
    """A compare / stats label with an optional generation: "golf 7" -> ("golf", 2012, 2020)."""
    tokens, span = generation_hint(_search_tokens(label or ""))
    search = canonical_search(clean_search(" ".join(tokens)))
    return (search, span[0], span[1]) if span else (search, None, None)


# Names people use that never appear in a listing's make/model. Each maps to a canonical search
# (shown in titles and read back from them) and the SQL LIKE pattern used on the folded
# "make model" label. Mercedes stores "C 220", "E 350"...; BMW stores "320d", "530"...
_CLASS = r"(?:klas\w*|class|klase)"
MODEL_ALIASES = tuple(
    [(rf"\b(?:mercedes(?:[ -]benz)?\s+|benz\s+)?{c}[ -]?{_CLASS}\b|\b(?:mercedes|benz)\s+{c}\b(?![ -]?[0-9])",
      f"mercedes {c} klasa", f"%benz {c} _%") for c in "abces"]
    + [(rf"\b(?:bmw\s+)?(?:serij\w*|series)\s*{n}\b|\b(?:bmw\s+)?{n}\s*(?:serij\w*|series|er)\b",
        f"bmw serija {n}", f"%bmw {n}%") for n in "1357"]
    + [(r"^vw$", "volkswagen", "%volkswagen%"), (r"^vw\s+", None, None)]
)


def canonical_search(search):
    """Rewrite a known alias ("Mercedes C klasa", "Serija 3", "vw golf") to the search that matches the data."""
    folded = fold(search or "").strip()
    for pattern, canonical, _ in MODEL_ALIASES:
        if re.search(pattern, folded):
            if canonical is None:  # "vw golf": the model word alone matches
                return clean_search(re.sub(pattern, "", folded))
            return canonical
    return search


def alias_pattern(search):
    """The LIKE pattern for a canonical alias search, or None."""
    folded = fold(search or "").strip()
    for pattern, canonical, like in MODEL_ALIASES:
        if canonical is not None and (folded == canonical or re.search(pattern, folded)):
            return like
    return None


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
    elif (re.search(r"\b(?:or|ili)\b", folded) and text_value.strip().endswith("?")
          and not re.search(r"[0-9]", folded) and len(folded.split()) <= 8
          and not _attribute(folded, _FUELS)[1] and not _attribute(folded, _GEARBOXES)[1]):
        candidates = _COMPARE_SPLIT.split(text_value)  # "Octavia ili Passat za porodicu?"
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
    r"^(?:and|also|what about|how about|and what about|a sta je sa|sta sa|a|i|only|just|isto|same)\b", re.I)


def looks_like_followup(text_value):
    """True when a message reads as a refinement of the previous answer.

    Used only for the best-effort memory of a private chat: filter-only messages
    ("from 2018", "under 12000") or ones opening with "what about ...".
    """
    original = strip_mentions((text_value or "")[:MAX_QUESTION]).strip()
    if _FOLLOWUP_CUE.match(original):
        return True
    filters, rest = _extract_filters(fold(original), original)
    return bool(filters.numeric() or filters.include_old or filters.attributes()) and not _search_tokens(rest)


# "I meant / I said / no, rather ..." — the message corrects the previous answer's search.
_CORRECTION = re.compile(
    r"\b(?:mislim da sam|mislio sam|mislila sam|htio sam|htjela sam|rekao sam|rekla sam|napisao sam|"
    r"napisala sam|stavio sam|stavila sam|trazio sam|trazila sam|"
    r"nisam (?:trazio|trazila|rekao|rekla|stavio|stavila|napisao|napisala|mislio|mislila|htio|htjela)|"
    r"ne\s*,?\s*(?:nego|vec)|pogresno|krivo si|nije to (?:sto|sta|ono)|"
    r"i said|i meant|i asked for|i wrote|i wanted|no\s*,?\s*i (?:meant|said|want\w*)|not what i)\b")
# "... a ti mi pokazuješ škodu od 33k": what the bot showed, not what the user wants. Cut it off so
# its numbers are not read as new filters.
_COMPLAINT = re.compile(
    r"(?:^|[\s,;.!]+)(?:(?:a|ali|but|and|pa)\s+)?(?:ti\s+)?(?:mi\s+)?(?:si\s+)?(?:mi\s+)?"
    r"(?:pokazujes|pokazes|pokazao|pokazala|pokazuje|dajes|nudis|izbacujes|izbacio|izbacila|vracas)\b.*$"
    r"|(?:^|[\s,;.!]+)(?:(?:but|and)\s+)?you(?:'?re| are)?\s+(?:show|gave|give|giving|showing|showed|returned|sent|listed)\w*\b.*$",
    re.S)


def is_correction(text_value):
    """True when a message corrects the previous question ("mislim da sam stavio ...", "ne, nego ...")."""
    return _CORRECTION.search(fold(strip_mentions((text_value or "")[:MAX_QUESTION]))) is not None


# Titles the bot puts on its first line, per language. Replies are parsed back from them, so a
# follow-up works whichever language the answer was in. Kept equal to telegram_i18n (tested).
_TITLES = (
    {"trends": "Observed asking-price changes · last ", "trends_unit": "days",
     "budget": "Recently observed at or below ", "find": "Observed listings", "upto": "up to ", "from": "from ",
     "family": "for a family", "old": "incl. older", "suggest": "Data-based shortlist",
     "liquidity": "⏱ Fastest to disappear from our search", "new": "incl. new",
     "fuel": {"diesel": "diesel", "petrol": "petrol", "hybrid": "hybrid", "electric": "electric", "lpg": "LPG"},
     "gearbox": {"automatic": "automatic", "manual": "manual"}},
    {"trends": "Zabilježene promjene tražene cijene · zadnjih ", "trends_unit": "dana",
     "budget": "Nedavno viđeno do ", "find": "Viđeni oglasi", "upto": "do ", "from": "od ",
     "family": "za porodicu", "old": "i stariji", "suggest": "Uža lista na osnovu podataka",
     "liquidity": "⏱ Najbrže nestaju iz naše pretrage", "new": "i novi",
     "fuel": {"diesel": "dizel", "petrol": "benzin", "hybrid": "hibrid", "electric": "električni", "lpg": "LPG"},
     "gearbox": {"automatic": "automatik", "manual": "manuelni"}},
)
_DASH = r"\s*[–—-]\s*"


def _title_filters(body, titles):
    """Filters from the ' · '-separated pieces after an answer's title."""
    values = {"search": "", "budget": None, "year_min": None, "mileage_max": None, "family": False,
              "include_old": False, "price_min": None, "year_max": None, "fuel": "", "transmission": "",
              "new_ok": False}
    upto, since = titles["upto"], titles["from"]
    fuels = {label: value for value, label in titles["fuel"].items()}
    gearboxes = {label: value for value, label in titles["gearbox"].items()}
    for piece in body.split(" · "):
        piece = piece.strip()
        if not piece:
            continue
        price_span = re.fullmatch(rf"([0-9.]+){_DASH}([0-9.]+) KM", piece)
        year_span = re.fullmatch(rf"((?:19|20)[0-9]{{2}}){_DASH}((?:19|20)[0-9]{{2}})", piece)
        if piece == titles["family"]:
            values["family"] = True
        elif piece == titles["old"]:
            values["include_old"] = True
        elif piece == titles["new"]:
            values["new_ok"] = True
        elif piece in fuels:
            values["fuel"] = fuels[piece]
        elif piece in gearboxes:
            values["transmission"] = gearboxes[piece]
        elif price_span:
            values["price_min"] = _amount(price_span.group(1), None, PRICE_RANGE)
            values["budget"] = _amount(price_span.group(2), None, PRICE_RANGE)
        elif year_span:
            values["year_min"] = _amount(year_span.group(1), None, YEAR_RANGE)
            values["year_max"] = _amount(year_span.group(2), None, YEAR_RANGE)
        elif piece.startswith(upto) and piece.endswith(" KM"):
            values["budget"] = _amount(piece[len(upto):-3], None, PRICE_RANGE)
        elif piece.startswith(since) and piece.endswith(" KM"):
            values["price_min"] = _amount(piece[len(since):-3], None, PRICE_RANGE)
        elif piece.startswith(upto) and piece.endswith(" km"):
            values["mileage_max"] = _amount(piece[len(upto):-3], None, MILEAGE_RANGE)
        elif piece.startswith(since) and piece[len(since):].strip().isdigit():
            values["year_min"] = _amount(piece[len(since):], None, YEAR_RANGE)
        elif piece.startswith(upto) and piece[len(upto):].strip().isdigit():
            values["year_max"] = _amount(piece[len(upto):], None, YEAR_RANGE)
        else:
            values["search"] = clean_search(piece)
    return Filters(**values)


def _context_from_line(line):
    for titles in _TITLES:
        if line.startswith(titles["trends"]):
            match = re.match(rf"([0-9]{{1,2}}) {titles['trends_unit']}", line[len(titles["trends"]):])
            if match:
                return Intent("trends", days=max(1, min(int(match.group(1)), 90)))
            return None
        if line.startswith(titles["budget"]):
            body = line[len(titles["budget"]):]
            amount_part, _, search = body.partition(" · ")
            budget = _amount(re.sub(r"\s*KM$", "", amount_part), None, PRICE_RANGE)
            if budget:
                return Intent("find", Filters(clean_search(search), budget))
            return None
        if line.startswith(titles["find"]):
            return Intent("find", _title_filters(line[len(titles["find"]):], titles))
        if line.startswith(titles["suggest"]):
            filters = replace(_title_filters(line[len(titles["suggest"]):], titles), search="")
            return Intent("suggest" if filters.budget else "find", filters)
        if line.startswith(titles["liquidity"]):
            return Intent("liquidity", _title_filters(line[len(titles["liquidity"]):], titles))
    return None


def context_from_reply(reply_text):
    """Recover the previous answer's scope from the bot message being replied to.

    The title is normally the first line; when Gemini phrased the answer, its sentences come first
    and the database answer (with its title) follows, so the first line that reads as a title wins.
    """
    for line in (reply_text or "").strip().split("\n")[:40]:
        found = _context_from_line(line.strip())
        if found is not None:
            return found
    return None


_FOLLOW = r"\b(?:follow\w*|track\w*|watch\w*|prati\w*|zaprati\w*|pracenj\w*|praceni\w*)\b"
_SHORTLIST = r"\b(?:shortlist\w*|saved (?:cars?|ads?|listings?)|sacuvan\w*|sacuva\w*|moja lista|moj spisak|my list)\b"
_STATUS = r"\b(?:status|fresh\w*|up to date|stale|collector|how old|when was|azurn\w*|kolektor)\b"
_LATEST = r"\b(?:latest|newest|what'?s new|whats new|new listings|interesting|finds|najnovij\w*|sta ima novo|novo)\b"
_TRENDS = (r"\b(?:trend\w*|price changes?|prices? (?:drop|fall|rise|change)\w*|pojeftin\w*|poskup\w*)|"
           r"\bcijen\w*.*\b(?:mijenj|pad|rast)\w*|\b(?:mijenj|pad|rast)\w*.*\bcijen\w*")
_LIQUID = (r"\bnajlak\w*(?:\s+\w+){0,2}\s+prod\w*|\bnajbrz\w*(?:\s+\w+){0,2}\s+prod\w*|"
           r"\b(?:brzo|lako|lakse|brze)\s+(?:se\s+)?prod\w*|\b(?:naj)?likvid\w*|\beasiest to sell\b|"
           r"\bsells? (?:the )?(?:fastest|quickest)\b|\bquickest to sell\b|\bsell(?:s)? (?:fast|quickly)\b|"
           r"\bfastest (?:to )?sell\w*\b")
_GREETING = r"^(?:hej|hey|hi|hello|ej|bok|cao|zdravo|pozdrav|selam|merhaba|yo|sta ima|kako si|dobar dan|dobro jutro)\W*$"
_FORECAST = r"\b(?:will|predict\w*|forecast\w*|going to|next (?:year|month|week)|prognoz\w*|hoce li|budu[cć]\w*)\b"
_CURRENCY = r"\b(?:eur|euro|euros|usd|dollars?)\b|[€$]"
_ACK = r"^(?:thanks|thank you|thx|ty|ok|okay|hvala|super|great|nice|cool|sjajno)\W*$"
_SUGGEST = (r"\b(?:suggest\w*|recommend\w*|preporuc\w*|should i buy|what to buy|what (?:car|cars) (?:should|to)|"
            r"which car|best (?:buy|value|deal|car)s?|worth buying|smartest|najpametnij\w*|pametno\w*|"
            r"sta (?:da )?kupi\w*|sta kupiti|koji auto|najbolje (?:se )?prodaj\w*|najtrazenij\w*|"
            r"best sell\w*|what sells|sells? best|popular)\b")
_ADVICE = r"\b(?:should i buy|what (?:car|to buy)|which car|recommend\w*|best (?:car|deal)s?|worth buying|preporuc\w*|koji auto|sta kupiti|sta da kupim)\b"
_STATS = r"\b(?:how much|average|typical|median|cost\w*|price of|koliko|prosjecn\w*|cijena)\b"


# "je li ovo dobra cijena", "is this a good price", "koja je normalna cijena", "koliko košta prosječno":
# the user wants a price compared with similar cars, not a search capped at that price.
_PRICE_CHECK = (
    r"\b(?:dobr\w*|normaln\w*|fer|realn\w*|ok|okej|pristojn\w*|povoljn\w*|korektn\w*)\s+(?:je\s+)?cijen\w*|"
    r"\bcijen\w*\s+(?:je\s+)?(?:li\s+)?(?:dobr\w*|normaln\w*|fer|realn\w*|ok|okej|korektn\w*)\b|"
    r"\b(?:good|fair|reasonable|normal|typical|average|ok|decent)\s+(?:asking\s+)?price\b|"
    r"\bis (?:this|that|it) (?:a )?(?:good|fair|ok|decent|reasonable)\b|\bgood deal\b|\bprice check\b|"
    r"\bkoliko (?:kosta|kostaju|vrijedi|vrijede)\b.*\b(?:19|20)[0-9]{2}\b|\bprosjecn\w*\b|"
    r"\b(?:vrijedi|isplati) li\b|\bpreskup\w*|\boverpriced\b")
PRICE_CHECK_YEARS = 1  # compare with the same model year +- this many years


def _price_check(filters, folded):
    """A price-check Intent: the stated amount is the price to judge, never a cap on the comparison."""
    price = filters.budget or filters.price_min
    year = None
    year_min, year_max = filters.year_min, filters.year_max
    if year_min and not year_max:
        year = year_min
        year_min, year_max = year - PRICE_CHECK_YEARS, year + PRICE_CHECK_YEARS
    comparison = Filters(search=filters.search, year_min=year_min, year_max=year_max, fuel=filters.fuel,
                         transmission=filters.transmission, new_ok=filters.new_ok)
    if not filters.search:
        return Intent("clarify", comparison, note="price_check", price=price, year=year,
                      mileage=filters.mileage_max)
    if not (price or year_min or year_max or comparison.attributes() or filters.mileage_max):
        return Intent("model_stats", Filters(filters.search))  # "average price of a golf": all years, as before
    return Intent("price_check", comparison, price=price, year=year, mileage=filters.mileage_max)


def _model_search(rest, named):
    """The search text: a known alias ("C klasa", "Serija 3") wins, else the remaining model words."""
    folded_rest = fold(rest)
    for pattern, canonical, _ in MODEL_ALIASES:
        if canonical and re.search(pattern, folded_rest):
            return canonical
    return canonical_search(clean_search(" ".join(named)))


CORRECTABLE = frozenset({"find", "suggest", "liquidity"})


def km_reads_as_price(previous_filters, folded):
    """Rule for a correction: "50.000km do 60.000km" means KM when the previous question was about
    price (and not mileage) and the message itself says nothing about mileage."""
    return (bool(previous_filters.budget or previous_filters.price_min) and not previous_filters.mileage_max
            and re.search(_MILEAGE_CUES, folded) is None)


def _km_range_is_price(context, folded, original):
    """A bare "50.000km do 60.000km" right after a price question is a price range (M2b in the
    2026-11-15 sweep): a two-sided mileage range is not something the bot can search anyway."""
    if context is None or context.kind not in CORRECTABLE or not km_reads_as_price(context.filters, folded):
        return False
    span = _price_range(folded, original, False)
    return bool(span and span[1] == "mileage")


def stated_filters(text_value, context=None):
    """The price / year / mileage bounds a message states, read the same way ``parse_question``
    reads them (including the km-as-KM rule for a correction). Used to ground Gemini's reading."""
    original = strip_mentions((text_value or "")[:MAX_QUESTION]).strip()
    folded = fold(original)
    km_is_price = False
    if context is not None and context.kind in CORRECTABLE and _CORRECTION.search(folded):
        cut = _COMPLAINT.search(folded)
        if cut:
            original, folded = original[:cut.start()], folded[:cut.start()]
        km_is_price = km_reads_as_price(context.filters, folded)
    else:
        km_is_price = _km_range_is_price(context, folded, original)
    return _extract_filters(folded, original, km_is_price)[0]


def _correct(original, folded, context):
    """Merge a correction into the previous question's filters and keep its intent, or None."""
    cut = _COMPLAINT.search(folded)
    if cut:
        original, folded = original[:cut.start()], folded[:cut.start()]
    filters, rest = _extract_filters(folded, original, km_reads_as_price(context.filters, folded))
    named = [token for token in _search_tokens(rest) if not token.isdigit()]
    new = replace(filters, search=clean_search(" ".join(named)) if len(named) <= 3 else "")
    if not (new.numeric() or new.search or new.family or new.include_old or new.attributes()):
        return None
    merged = merge_filters(context.filters, new)
    kind = context.kind
    if kind == "suggest" and not merged.budget:
        kind = "find"
    return Intent(kind, merged, note="correction")


def parse_question(text_value, context=None, _restating=False):
    """Turn one question into an Intent. ``context`` is an Intent from a replied-to answer
    (or, for a correction, the same person's previous question)."""
    original = strip_mentions((text_value or "")[:MAX_QUESTION]).strip()
    original = re.sub(r"^/ask(?:@\w+)?\s*", "", original, flags=re.I).strip()
    if not original:
        return Intent("unsupported")
    folded = fold(original)

    if re.search(_ACK, folded):
        return Intent("ignore")
    if re.search(_GREETING, folded):
        return Intent("chitchat")

    if not _restating and _CORRECTION.search(folded):
        if context is not None and context.kind in CORRECTABLE:
            corrected = _correct(original, folded, context)
            if corrected is not None:
                return corrected
        else:
            # No earlier question to correct: read only what the user restates, never the part that
            # describes what the bot showed. Without a price or a model there is nothing to re-run.
            cut = _COMPLAINT.search(folded)
            if cut:
                original, folded = original[:cut.start()], folded[:cut.start()]
            restated = parse_question(original, _restating=True) if original.strip() else Intent("unsupported")
            if restated.kind in {"find", "suggest", "liquidity"} and (
                    restated.filters.search or restated.filters.budget or restated.filters.price_min):
                return restated
            return Intent("clarify", note="correction")
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

    filters, rest = _extract_filters(folded, original, _km_range_is_price(context, folded, original))
    unclear = _UNCLEAR[0]
    tokens = _search_tokens(rest)
    tokens, generation = generation_hint(tokens)
    named = tokens if any(not token.isdigit() for token in tokens) else []
    filters = replace(filters, search=_model_search(rest, named))
    if generation and not (filters.year_min or filters.year_max):
        filters = replace(filters, year_min=generation[0], year_max=generation[1])

    if re.search(_PRICE_CHECK, folded) and not re.search(_LIQUID, folded):
        return _price_check(filters, folded)

    if unclear and filters.budget is None and filters.mileage_max is None:
        # one tap decides: price (KM) or mileage (km)
        kind = ("liquidity",) if re.search(_LIQUID, folded) else ()
        return Intent("clarify", filters, note="km_unit", offset=unclear, labels=kind)

    if re.search(_LIQUID, folded):
        return Intent("liquidity", filters)

    if re.search(_SHORTLIST, folded) and not filters.search and filters.budget is None:
        return Intent("shortlist")

    if re.search(_FOLLOW, folded):
        generic = {"model", "modeli", "modela", "models", "cars", "auta", "automobili"}
        named = " ".join(word for word in filters.search.split() if fold(word) not in generic)
        if named and not filters.numeric():
            return Intent("follow", Filters(search=named))
        return Intent("following")

    model_words = [t for t in tokens if len(t) > 2 or any(c.isdigit() for c in t)]
    if filters.budget and re.search(_SUGGEST, folded) and (not model_words or len(model_words) >= 3):
        return Intent("suggest", replace(filters, search="", include_old=False))

    if filters.is_empty():
        if re.search(_ADVICE, folded):
            return Intent("clarify")
        if re.search(_STATUS, folded):
            return Intent("status")
        if re.search(_LATEST, folded):
            return Intent("latest")

    if context and context.kind == "trends" and tokens and all(t.isdigit() for t in tokens):
        return Intent("trends", days=max(1, min(int(tokens[0]), 90)))

    if len(tokens) > 4 or (not filters.numeric() and len(original.split()) >= 9):
        return Intent("unsupported")  # a sentence, not a car name

    if context and context.kind == "find" and (not filters.is_empty() or filters.include_old):
        return Intent("find", merge_filters(context.filters, filters))

    if (context and context.kind in {"suggest", "liquidity"} and (filters.numeric() or filters.attributes())
            and not filters.search
            and (context.kind != "suggest" or context.filters.budget or filters.budget)):
        # "a od 2020?" in reply to a shortlist: same question, narrower bounds
        return Intent(context.kind, merge_filters(context.filters, filters))

    if not filters.is_empty():
        if (filters.search and not filters.family and not filters.numeric() and re.search(_STATS, folded)):
            return Intent("model_stats", filters)
        return Intent("find", filters)

    return Intent("unsupported")
