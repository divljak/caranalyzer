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


MAX_QUESTION = 1000  # a voice-to-text message can run past 400 characters with the request at the very end
PAGE_SIZE = 5  # listings per page (telegram_bot.PAGE_SIZE is the same number)
MAX_PAGE_OFFSET = 95
MAX_SEARCH = 40
PRICE_RANGE = (1_000, 1_000_000)
YEAR_RANGE = (1980, 2035)
MILEAGE_RANGE = (1_000, 1_000_000)
BARE_KM_ASK_MAX = 100_000  # "do 200000km" is mileage: nobody caps a used-car price that high, so it is not asked

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
    mileage_min: int | None = None  # "preko 200.000 km": at least this many km; ``mileage_max`` is the upper bound
    low_km: bool = False  # "malo kilometara": a soft preference, lowest mileage first (never a hard filter)
    cheapest: bool = False  # "najjeftiniji golf": a model search listed cheapest first (never a filter)

    @property
    def price_max(self):
        """The upper price bound. Stored as ``budget`` because every older button and answer uses that name."""
        return self.budget

    def numeric(self):
        """True when any price, year or mileage bound is set."""
        return bool(self.budget or self.price_min or self.year_min or self.year_max or self.mileage_max
                    or self.mileage_min)

    def attributes(self):
        """True when a fuel, gearbox or low-mileage preference is set."""
        return bool(self.fuel or self.transmission or self.low_km)

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
    mileage_min, mileage_max = bounds(base.mileage_min, base.mileage_max, new.mileage_min, new.mileage_max)
    return Filters(
        search=new.search or base.search,
        budget=budget,
        year_min=year_min,
        mileage_max=mileage_max,
        mileage_min=mileage_min,
        low_km=new.low_km or base.low_km,
        family=new.family or base.family,
        include_old=new.include_old or base.include_old,
        price_min=price_min,
        year_max=year_max,
        fuel=new.fuel or base.fuel,
        transmission=new.transmission or base.transmission,
        new_ok=new.new_ok or base.new_ok,
        cheapest=new.cheapest or base.cheapest,
    )


@dataclass(frozen=True)
class Intent:
    kind: str  # status|latest|trends|find|compare|model_stats|price_check|liquidity|deals|clarify|unsupported
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
    # find only: how many matches the page shown had in all, so "pokaži još" knows when there is no more
    page_total: int | None = None


# Serbian Cyrillic (plus the few Russian letters people mix in) to Latin: "Голф до 15000 КМ" reads as
# "Golf do 15000 KM". Applied to every incoming message (``strip_mentions``); ``fold`` keeps one letter each.
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "đ", "е": "e", "ж": "ž", "з": "z", "и": "i", "ј": "j",
    "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n", "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "ћ": "ć", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "č", "џ": "dž", "ш": "š", "й": "j", "ы": "i", "э": "e",
    "ю": "ju", "я": "ja", "щ": "šč", "ъ": "", "ь": "", "ё": "jo", "є": "je", "і": "i", "ї": "ji",
}


def to_latin(value):
    """Transliterate Cyrillic letters, keeping capitals ("КМ" -> "KM", "Љубушки" -> "Ljubuški")."""
    if not value or not re.search("[Ѐ-ӿ]", value):
        return value or ""
    out = []
    for char in value:
        latin = _CYRILLIC.get(char.lower())
        if latin is None:
            out.append(char)
        elif char.isupper():
            out.append(latin[:1].upper() + latin[1:])
        else:
            out.append(latin)
    return "".join(out)


def fold(value):
    """Lower-case and strip diacritics while keeping string length unchanged."""
    out = []
    for char in (value or ""):
        lowered = char.lower()[:1] or char  # "İ".lower() is two characters: keep one per input character
        if lowered in _CYRILLIC:
            lowered = (_CYRILLIC[lowered] or " ")[0]
        if lowered == "đ":
            out.append("d")
            continue
        base = unicodedata.normalize("NFKD", lowered)
        out.append(base[0] if base else lowered)
    return "".join(out)


def clean_search(value):
    value = re.sub(r"[^\w\s.\-]|_", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()[:MAX_SEARCH].strip()


# "15.000", "15 000", "7,5k" / "7.5 hiljada" (a decimal only before a thousands word), "15"
_THOUSAND_WORD = r"k(?![a-z0-9])|h?iljad\w*|tisuc\w*|thousand\w*|soma\b|som\b"
_NUMBER = (r"[0-9]{1,3}(?:[., ][0-9]{3})+(?![0-9])|[0-9]{1,3}[.,][0-9]{1,2}(?=\s*(?:" + _THOUSAND_WORD + r"))|[0-9]+")
_AMOUNT = r"(" + _NUMBER + r")\s*(" + _THOUSAND_WORD + r")?"


def _amount(digits, thousands, bounds):
    decimal = re.fullmatch(r"([0-9]{1,3})[.,]([0-9]{1,2})", (digits or "").strip())
    if decimal and thousands:  # "7,5k" is 7.500, never 75.000 or a "7" and a "5k"
        value = int(decimal.group(1)) * 1000 + int(decimal.group(2).ljust(3, "0"))
        return value if bounds[0] <= value <= bounds[1] else None
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
ne nije se ovo ono ovu gore nikako nikada nikad never probaj probati probaci probajte pogledaj this above try mileage kilometraza kilometraze
kilometers kilometres kilometara kilometar kilometri kilometre kms najlakse najbrze prodati prodam lako brzo staro stari staru godina godine godinu
godiste godista godistem izmedu izmedju between hiljada hiljade hiljadu tisuca tisuce thousand
mislim mislio mislila stavio stavila stavili napisao napisala rekao rekla trazio trazila nisam nego
pogresno krivo meant mean said asked wrote wanted wrong
normal normalna normalnu normalno normalan normalne realna realnu realno realan realne fer fair reasonable
typical average avg prosjek prosjeku prosjecno prosjecna prosjecnu prosjecan prosjecne cheapest
najjeftiniji najjeftinija najjeftinije najjeftinijeg najjeftinijih jeftin jeftina jeftino jeftini jeftinije
expensive skup skupa skupo skupi kosta kostaju kosti kostao vrijedi vrijede worth treba trebao trebala trebalo
isto same that thats generacija generacije generation gen model modela modeli verzija iljada iljade
is isn its it's price prices good fine okej ovog ovom taj tog
bilo kolko kolika koliki najvise najmanje not most registracija registracije registraciju registracijom registrovan ponuda ponude ponudu ponudi offer offers ove ovih sedmice sedmici tjedna tjedan
""".split()) | set("""
pozdrav pozdravi pozdravljam pozz poz zdravo selam selamun aleikum alejkum alejkumu merhaba hvala fala falim unaprijed
unapred puno brate brat buraz burazeru ekipa ekipo ljudi molim hitno hitan hitna dajte zanima zanimaju zanimalo
interesuje interesuju interesira imal imas imate imaju imali kakav kakvu kakve kakvi kakvog uzmem uzmes uzeti uzmi
uzimam kupio kupila kupujem kupovati kupis ipak evo eto ovako tako takvo slicno slicnog dvoje troje djece putujemo
putujem putovanja bio bila bude kola kolima autic autica auta kad kada pojavi pojave pojavio javi javite obavijesti
obavijestite obavjesti obavijest obavjestite su smo ste iz kod ovdje tu tamo nekakav nekog neku nove novi nova novu
novog novih nov polovni polovno polovan polovna polovne polovnog polovnih rabljeni rabljen rabljena rabljeno rabljenog
koristen koristeni koristena koristenog nekoristen nekoristeni nekoristena nekoristenog skoro gotovo maksimalno
maksimum minimalno minimum novija noviji novijeg novijih mladji mladja mladje grad gradsku gradska gradski gradsko
voznju voznja skuplje skuplji skuplja skupljeg skuplju povoljnije povoljniji povoljnija izvan budzeta budzetu budzetom isplati isplativ isplativo isplativa sacuvane sacuvaj sacuvana sacuvanih sacuvam sacuvati presao presla
preslo presli prede predje predjeno predeno predena vozen vozeno navozeno mozda prvi prva prvo prve prva dva tri
drugi druga drugo drugu ostali ostale ostalo other another wanted pls plz please thanks
marka marku marke marki posao poslu
hajde ajde ajmo hajmo hajdemo ajdemo uzmemo uzmimo uzmem uzmi uzimamo obzir obzira obzirom razmotri razmotrimo razmotrite
razmatramo gledamo gledajmo gledati platforma platforme platformi platformu platformom prelazi prelazim prelazis prelaziti
prelazila prelazio prelazilo prelaze premasuje premasi prekoracuje najprodavanije najprodavaniji najprodavanija
ide idu recimo npr naprimjer znaci dakle sada sad tada opet ponovo onako ovdje ukljucujuci uracunaj uracunati
""".split())

# Words that start a question or describe a price, never part of a make or model ("prosječno",
# "najjeftiniji", "normalna"): dropped from the search text whatever their ending.
_FILLER_PREFIXES = ("regist", "prosjec", "najjeftin", "jeftin", "normaln", "realn", "najpovoljn", "povoljn",
                    "odlicn", "stanj", "ocuvan", "garazir", "uvoz", "servisn",
                    "prelaz", "premasuj", "prekorac", "razmot", "razmatr", "uracun")  # "u odličnom stanju", "iz uvoza"

_MENTION = re.compile(r"@\w+")
_MILEAGE_CUES = (r"(?:mileage|kilometraz\w*|kilometers|kilometres|odvozen\w*|prijeden\w*|on the clock|"
                 r"pre(?:sa|sl)\w*|predj?(?:e|en)\w*|navozen\w*|vozen\w*)")


def strip_mentions(value):
    """The message without @mentions, with Cyrillic transliterated and whitespace collapsed."""
    return re.sub(r"\s+", " ", _MENTION.sub(" ", to_latin(value or ""))).strip()


# Places the listings cannot be filtered by yet. They are cut out of a question (with the preposition before
# them: "iz Mostara", "u Banjaluci", "sa područja Tuzle", "kod Sarajeva", "Istočno Sarajevo", "u RS"), so they
# never become part of the model or get "repaired" into one (Doboj -> Doblo); the answer then says that location
# is not a filter. Matched on folded text by stem, so every case ending is covered.
_PLACE_NAMES = (
    r"sarajev\w*|mostar\w*|banj\w*\s*luk\w*|banjaluc\w*|tuzl\w*|zenic\w*|bihac\w*|doboj\w*|brck\w*|bijeljin\w*|"
    r"trebinj\w*|livn\w*|travnik\w*|cazin\w*|konjic\w*|gorazd\w*|kakanj\w*|zvornik\w*|prijedor\w*|gradacc?\w*|"
    r"gracanic\w*|lukavc\w*|lukavac|srebrenik\w*|tesanj\w*|tesnj\w*|maglaj\w*|zavidovic\w*|jajc\w*|bugojn\w*|"
    r"capljin\w*|ljubusk\w*|posusj\w*|sirok\w* brijeg\w*|gradisk\w*|modric\w*|dervent\w*|kiseljak\w*|vogosc\w*|"
    r"ilidz\w*|hadzic\w*|fojnic\w*|vitez\w*|sansk\w* most\w*|velik\w* kladus\w*|kladus\w*|neum\w*|stolc\w*|"
    r"hercegovin\w*|bosansk\w* krajin\w*|krajin\w*|posavin\w*|semberij\w*|podrinj\w*|zagreb\w*|beograd\w*|"
    r"njemack\w*|austrij\w*|svedsk\w*|fbih|federacij\w*|republik\w* srpsk\w*|cijel\w* bih|cijel\w* bosn\w*")
_PLACE = re.compile(
    r"(?:\b(?:u|iz|kod|oko|blizu|po|sa|s|na)\s+)?(?:\b(?:podrucj\w*|okolin\w*|regij\w*|opcin\w*|kanton\w*)\s+)?"
    r"(?:\b(?:istocn\w*|novo|novi|stari|staro)\s+)?\b(?:" + _PLACE_NAMES + r")\b"
    r"|\b(?:u|iz|po|na podrucju)\s+rs\b")


def strip_places(value):
    """(text with place names blanked out, True if any). Length is kept, so spans still line up with ``fold``."""
    folded = fold(value)
    spans = [match.span() for match in _PLACE.finditer(folded)]
    if not spans:
        return value, False
    chars = list(value)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars), True


def is_place(token):
    """True when a single word is a place name ("doboj", "mostaru"): never a model, never typo-repaired."""
    return re.fullmatch(_PLACE_NAMES, fold(token or "")) is not None


_FAMILY = (r"\b(?:porodic\w*|familij\w*|famil(?:y|ies)|djec\w*|dijete|djete|kids?|children|karavan\w*|"
           r"monovolumen\w*|kombi\w*|wagon|estate|mpv|minivan|prostran\w*|roomy|spacious|sedmosjed\w*|7 sjedist\w*|"
           r"combi|avant|varijant\w*|variant|touring|sportwagon|sports? tourer|tourer|sw)\b")
_INCLUDE_OLD = (r"\b(?:(?:i |ukljuci (?:i )?|pokazi (?:i )?|sa |s )starij\w*|"
                r"include (?:the )?older|older (?:ones|ads|listings) too|with older|incl\w* old\w*)\b")


_FUELS = (
    ("diesel", r"\b(?:dizel\w*|diesel\w*|dizl\w*|tdi|tdci|hdi|dci|crdi|cdi|jtd\w*|multijet)\b"),
    ("petrol", r"\b(?:benzin\w*|benc\w*|petrol|gasoline|tsi|tfsi)\b"),
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
             r"\bbrand new\b|\b0\s*km\b|\bnula kilometar\w*|\bfabricki nov\w*|\b(?:nov\w*\s+)?iz salona\b|"
             r"\bnekoristen\w*|\bneregistrovan\w*\s+nov\w*")
# "polovni", "rabljeni", "korišteni": used cars, the default; after a new-car search it switches back to used.
_USED = r"\b(?:polovn\w*|polovan|rabljen\w*|koristen\w*|used|second[- ]hand)\b"
# Body types the listings do not record: never a model, the answer says it is not a filter.
_BODY = (r"\b(?:suv\w*|dzip\w*|terenac|terenc\w*|limuzin\w*|kabriolet\w*|kabrio|cabrio\w*|convertible|"
         r"hatchback\w*|hecbek\w*|pick\s*-?\s*up\w*|pikap\w*|coupe|sedan\w*|crossover\w*|krosover\w*)\b")


def _attribute(folded, table):
    """(value, matches) for the single fuel or gearbox a message names; ('', ()) when none or conflicting."""
    found = [(value, match) for value, pattern in table for match in re.finditer(pattern, folded)]
    values = {value for value, _ in found}
    if len(values) != 1:
        return "", tuple(match for _, match in found) if len(values) > 1 else ()
    return values.pop(), tuple(match for _, match in found)


def wants_family(folded):
    return re.search(_FAMILY, folded) is not None


def wants_new(folded):
    """"novi auto", "nov golf 8", "iz salona", "nekorišten passat": new cars stay in. "skoro nov" (nearly new) does not."""
    if re.search(_NEW_CARS, folded):
        return True
    for match in re.finditer(r"\b(?:nov|novi|novu|nova|novog|novo|new)\s+([a-z]+)", folded):
        if re.search(r"\b(?:skoro|gotovo|kao|ko|almost|like|nearly)\s+$", folded[:match.start()]):
            continue
        word = match.group(1)
        if word in _KNOWN_MODELS or word in MAKE_MODELS or word in GENERATIONS or _lemma(word) != word:
            return True
    return False


def wants_older(folded):
    return re.search(_INCLUDE_OLD, folded) is not None


_RANGE_FROM = r"(?:(?P<lead>od|from|izmedj?u|between)\s*)?"
_RANGE_SEP = r"\s*(?P<sep>-|–|—|do|to|until|i|and)\s*"
_YEAR_SPAN = re.compile(
    _RANGE_FROM + r"\b(?P<a>(?:19|20)[0-9]{2})" + _RANGE_SEP + r"(?P<b>(?:19|20)[0-9]{2})\b"
    r"(?!\s*(?:k(?![a-z])|km|bam|kam|marak\w*|hiljad\w*|tisuc\w*|thousand|eur\w*|[.,]?000))")
# "do 2018", "prije 2018", "before 2018", "starije od 2018": the newest model year, not a price
_YEAR_CEILING = (r"(?:\bdo|\bprije|\bpre|\bbefore|\buntil|\bup to|\bstarij\w*\s+od|\bolder than)\s*"
                 r"((?:19|20)[0-9]{2})\b(?!\s*(?:k(?![a-z])|km|bam|kam|marak\w*|hiljad\w*|tisuc\w*|thousand|eur\w*|[.,]?000))")
_THOUSANDS = r"k(?![a-z0-9])|h?iljad\w*|tisuc\w*|thousand|soma\b|som\b"
# "preko 40k", "iznad 30.000 KM", "over 20k": a lower price bound only.
_NOT_LESS = (r"(?:ne|nikako|no|not|never)\s+(?:manje|ispod|less|under|below|fewer)(?:\s+(?:than|od))?")
_ABOVE = (r"(?<!\bne )(?<!\bno )(?<!\bnot )\b(?:preko|iznad|vise od|vece od|over|above|more than|at least|najmanje|"
          r"minimalno|minimum|" + _NOT_LESS + r")\s*")
_PRICE_UNIT = r"km|bam|kam|maraka|marke|marks?"
_PRICE_SPAN = re.compile(
    r"(?<![a-z0-9.,])" + _RANGE_FROM
    + r"(?P<a>" + _NUMBER + r")\s*(?P<ma>" + _THOUSANDS + r")?\s*(?P<ua>" + _PRICE_UNIT
    + r")?(?![a-z0-9])" + _RANGE_SEP
    + r"(?P<b>" + _NUMBER + r")\s*(?P<mb>" + _THOUSANDS + r")?\s*(?P<ub>" + _PRICE_UNIT
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


# "preko 200.000 km", "više od 300 hiljada km", "od 100000 km": the number is a minimum.
_MIN_CUE = (r"(?:preko|iznad|vise od|vece od|over|above|more than|at least|najmanje|minimalno|minimum|min|od|from)"
            r"\s*(?:oko\s*)?$")
_NEGATED = r"(?:no|not|ne|nikako|never)\s+(?:more|vise|preko|iznad|over|above|vece|bigger|higher)(?:\s+(?:than|od))?"
_NOT_LESS_CUE = _NOT_LESS + r"\s*(?:oko\s*)?$"  # "ne manje od 100000 km": a minimum, whatever "manje od" says
_MAX_CUE = (r"(?:manje|less|under|ispod|below|max\w*|do|up to|upto|until|najvise|at most|" + _NEGATED
            + r")\s*(?:od|than)?\s*(?:oko\s*)?$")
# "malo kilometara", "mala kilometraza", "low mileage": a preference for few km, not a bound.
_LOW_KM = (r"\b(?:malo|mala|mali|manje|manjom|manjim|nisk\w*|nizak|low|lower|less|few|fewer|little)\s+(?:km|kms|kilometar\w*|kilometr\w*|mileage|"
           r"kilometers|kilometres)(?![a-z0-9])")
_KM_WORDS = r"kilometar\w*|kilometr\w*|kilometers|kilometres|kms"
_MILEAGE_WORD_AFTER = re.compile(rf"\s*(?:{_KM_WORDS})(?![a-z0-9])")


def _generation_digits(folded):
    """Indexes of the digits of a model generation ("golf 7", "passat b8", "golf7"): never an amount."""
    found = set()
    for match in re.finditer(r"\b([a-z]+)\s*([a-z]?[0-9])(?![0-9])", folded):
        model = _generation_model(match.group(1))
        if model and match.group(2) in GENERATIONS[model]:
            found.update(i for i in range(match.start(2), match.end(2)) if folded[i].isdigit())
    return found


def _span_number(value):
    """"15.000" -> 15000, "7,5" -> 7.5 (a decimal is only valid with a thousands word), None when empty."""
    decimal = re.fullmatch(r"([0-9]{1,3})[.,]([0-9]{1,2})", value or "")
    if decimal:
        return float(f"{decimal.group(1)}.{decimal.group(2)}")
    digits = re.sub(r"[^0-9]", "", value or "")
    return int(digits) if digits else None


def _price_range(folded, original, blocked=()):
    """A two-sided amount: ('od 50 - 60k' | '50.000 do 60.000 KM' | 'izmedu 50 i 60 hiljada').

    Returns (match, kind, low, high) where kind is "price" or "mileage", or None. A shared "k" /
    "hiljada" applies to both ends ("50-60k" is 50.000-60.000). Only all-lower-case "km"
    means mileage; any form with an upper-case K ("KM", "Km", "kM") is the currency.
    """
    for match in _PRICE_SPAN.finditer(folded):
        if match.group("sep") in {"i", "and"} and not match.group("lead"):
            continue
        if any(i in blocked for i in range(match.start(), match.end())):
            continue  # part of a year span or a generation ("golf 7"): not an amount
        units = [original[match.start(g):match.end(g)] for g in ("ua", "ub") if match.group(g)]
        if len(units) == 2 and (units[0] == "km") != (units[1] == "km"):
            continue  # "12000 KM do 200000 km": a price and a mileage, not one range
        if units and not match.group("ub") and not match.group("lead") and match.group("sep") in {"do", "to", "until"}:
            continue  # "ispod 200k km do 12k": a mileage, then a price cap, not one range
        many_a, many_b = bool(match.group("ma")), bool(match.group("mb"))
        low, high = _span_number(match.group("a")), _span_number(match.group("b"))
        if low is None or high is None:
            continue
        if many_a or (many_b and low < 1000):
            low *= 1000
        if many_b or (many_a and high < 1000):
            high *= 1000
        if low != int(low) or high != int(high):
            continue  # a decimal without a thousands word ("2.5 do 3") is not an amount
        low, high = int(low), int(high)
        if _MILEAGE_WORD_AFTER.match(folded, match.end()):
            kind, bounds = "mileage", MILEAGE_RANGE  # "100 do 200 hiljada kilometara"
        elif "km" in units:
            kind, bounds = "mileage", MILEAGE_RANGE
        else:
            kind, bounds = "price", PRICE_RANGE
        low, high = sorted((low, high))
        if bounds[0] <= low < high <= bounds[1]:
            return match, kind, low, high
    return None


# "da cijena ne prelazi (preko) 30k KM", "ne ide preko 30k": an upper bound, even though "preko" alone is a floor.
_NOT_OVER = re.compile(r"\b(?:ne|nikako ne)\s+(?:prelaz\w*|premasuj\w*|prekoracuj\w*|ide|idu|bude|bila|bilo|bi bila)"
                       r"(?:\s+(?:preko|iznad|vise od|vece od|over|above))?(?=\s*(?:oko\s*)?[0-9])")


def _extract_filters(folded, original):
    """Return Filters plus the original text with recognised spans removed.

    Unit case rule: all-lower-case "km" after an amount is mileage; "KM" / "Km" / "kM" is a price.
    """
    # Same length, so the indexes into ``original`` stay valid: the phrase now reads as a plain "do".
    folded = _NOT_OVER.sub(lambda m: "do".ljust(len(m.group())), folded)
    mask = [True] * len(original)

    def consume(match):
        for index in range(match.start(), match.end()):
            mask[index] = False

    year = year_max = None
    span = _year_range(folded)
    if span:
        consume(span[0])
        year, year_max = span[1], span[2]
    # A lone year ("Golf 2015") is that model year only; "od 2015", "2015+", "novije od 2015" are a lower bound
    # and "do 2018", "prije 2018" an upper one. (pattern, bound) where bound is "min", "max" or "exact".
    for pattern, bound in (() if span else (
        (r"(?:from|since|after|newer than|od|iza|nakon|novij\w* od|mladj\w* od)\s*((?:19|20)[0-9]{2})\b", "min"),
        (r"\b((?:19|20)[0-9]{2})\s*(?:\+|or newer|or later|and newer|ili novije|i novije)", "min"),
        (_YEAR_CEILING, "max"),
        (r"\b((?:19|20)[0-9]{2})\b(?!\s*(?:k(?![a-z])|km|bam|kam|marak\w*|hiljad\w*|tisuc\w*|thousand|eur\w*))",
         "exact"),
    )):
        match = re.search(pattern, folded)
        if match:
            candidate = int(match.group(1))
            if YEAR_RANGE[0] <= candidate <= YEAR_RANGE[1]:
                if bound == "max":
                    year_max = candidate
                else:
                    year = candidate
                    year_max = candidate if bound == "exact" else None
                consume(match)
                break

    if year is None and year_max is None:
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

    mileage = mileage_min = None
    budget = price_min = None
    low_km = False
    low = re.search(_LOW_KM, folded)
    if low:  # "malo kilometara": a preference, never search text
        low_km = True
        consume(low)

    def set_mileage(value, start):
        """Store a mileage bound: "preko / više od / od N km" is a minimum, anything else a maximum."""
        nonlocal mileage, mileage_min
        before = folded[:start]
        if re.search(_NOT_LESS_CUE, before) or (re.search(_MIN_CUE, before) and not re.search(_MAX_CUE, before)):
            if mileage_min is None:
                mileage_min = value
        elif mileage is None:
            mileage = value

    blocked = {i for i, keep in enumerate(mask) if not keep} | _generation_digits(folded)
    for _ in range(2):  # a price range and a mileage range can both be in one message, in either order
        price_span = _price_range(folded, original, blocked)
        if not (price_span and all(mask[i] for i in range(price_span[0].start(), price_span[0].end()))):
            break
        if price_span[1] == "price" and budget is None:
            price_min, budget = price_span[2], price_span[3]
        elif price_span[1] == "mileage" and mileage is None:
            mileage_min, mileage = price_span[2], price_span[3]
        else:
            break
        consume(price_span[0])
        blocked |= set(range(price_span[0].start(), price_span[0].end()))
    # A number followed by "km" written in lowercase is mileage ("do 20000km"); any form with an upper-case
    # K ("KM", "Km", "kM"), "maraka" or "BAM" is a price. "kilometara" / "hiljada kilometara" always names
    # mileage, whatever the case. Matched on the folded text ("100 tisuća km" has a "ć"), the unit's case
    # read from the original.
    for match in re.finditer(rf"(?<![0-9.,]){_AMOUNT}\s*(km|{_KM_WORDS})(?![a-z0-9])", folded):
        if not all(mask[i] for i in range(match.start(), match.end())):
            continue
        if original[match.start(3):match.end(3)].lower() == "km" and original[match.start(3):match.end(3)] != "km":
            continue  # a price
        candidate = _amount(match.group(1), match.group(2).lower() if match.group(2) else None, MILEAGE_RANGE)
        if candidate:
            set_mileage(candidate, match.start())
            consume(match)
    unconsumed = "".join(char if keep else " " for char, keep in zip(folded, mask))  # no "low mileage" here
    for pattern in (
        rf"{_AMOUNT}\s*(?:km|kilometara)?\s*(?:{_MILEAGE_CUES}|or less|max)\b",
        rf"{_MILEAGE_CUES}\D{{0,16}}?{_AMOUNT}\s*(?:km|kilometara)?",
    ):
        if mileage is not None:
            break
        match = re.search(pattern, unconsumed)
        if match and all(mask[i] for i in range(match.start(1), match.end(1))):
            candidate = _amount(match.group(1), match.group(2), MILEAGE_RANGE)
            if candidate:
                set_mileage(candidate, match.start(1))
                consume(match)
                break

    if price_min is None and budget is None:
        # "preko 40k" / "iznad 30.000 KM" / "over 20k": only a lower bound. A lower-case "km" after the
        # amount is mileage, which has no lower bound here, so it is left alone.
        for match in re.finditer(rf"{_ABOVE}(?<![0-9]){_AMOUNT}\s*(km|bam|kam|maraka|marke|marks?|eur\w*)?(?![a-z])",
                                 folded):
            if not all(mask[i] for i in range(match.start(), match.end())):
                continue
            if match.group(3) and original[match.start(3):match.end(3)] == "km":
                continue
            candidate = _amount(match.group(1), match.group(2), PRICE_RANGE)
            if candidate:
                price_min = candidate
                consume(match)
                break

    spans = [m for m in re.finditer(rf"(?<![0-9]){_AMOUNT}\s*(km|bam|kam|maraka|marke|marks|eur|euro|kn|kuna|kune|hrk)?(?![a-z])", folded)
             if all(mask[i] for i in range(m.start(), m.end()))]
    unitless = None
    for match in spans:
        if budget is not None:
            break
        candidate = _amount(match.group(1), match.group(2), PRICE_RANGE)
        if candidate:
            budget = candidate
            if not match.group(2) and not match.group(3):
                unitless = candidate  # "golf do 150 000": neither "KM" nor "k" says it is a price
            consume(match)
            break

    fuel, fuel_spans = _attribute(folded, _FUELS)
    gearbox, gearbox_spans = _attribute(folded, _GEARBOXES)
    for match in (list(re.finditer(_FAMILY, folded)) + list(re.finditer(_INCLUDE_OLD, folded))
                  + list(re.finditer(_LIQUID, folded)) + list(re.finditer(_NEW_CARS, folded))
                  + list(re.finditer(_DEALS, folded)) + list(re.finditer(_BODY, folded))
                  + list(fuel_spans) + list(gearbox_spans)):
        consume(match)
    rest = "".join(char if keep else " " for char, keep in zip(original, mask))
    if mileage_min and mileage and mileage_min > mileage:
        mileage_min = None
    filters = Filters(budget=budget, year_min=year, mileage_max=mileage, family=wants_family(folded),
                      include_old=wants_older(folded), price_min=price_min, year_max=year_max,
                      fuel=fuel, transmission=gearbox, new_ok=wants_new(folded),
                      mileage_min=mileage_min, low_km=low_km)
    _UNITLESS[0] = unitless
    return filters, rest


_UNITLESS = [None]  # a price cap read from a bare number with no unit or "k" in the last parse


def _singular(token):
    """Drop a plural ending so 'golfs' / 'golfovi' search as 'golf' (LIKE is substring-based)."""
    lowered = token.lower()
    if len(lowered) > 5 and lowered.endswith(("ovi", "evi", "ove")):
        return token[:-3]
    if len(lowered) > 4 and lowered.endswith("s") and not lowered.endswith(("ss", "es", "us", "is")):
        return token[:-1]
    return token


# Names people say or misspell that are never in an ad: the word the ads use instead.
SLANG = {
    "bembara": "bmw", "bemba": "bmw", "beemve": "bmw", "bmvee": "bmw", "merdzo": "mercedes", "mercedez": "mercedes",
    "pezo": "peugeot", "pezoo": "peugeot", "pezej": "peugeot", "reno": "renault",
    "klio": "clio", "megan": "megane", "folksvagen": "volkswagen", "folksvagn": "volkswagen", "folcvagen": "volkswagen",
    "folkswagen": "volkswagen", "volksvagen": "volkswagen", "wolkswagen": "volkswagen", "oktavija": "octavia",
    "oktavia": "octavia", "oktavu": "octavia", "skodu": "skoda", "skodom": "skoda", "skode": "skoda",
    "trojka": "serija 3", "trojku": "serija 3", "petica": "serija 5", "peticu": "serija 5",
}
# "golf vii", "golf mk7", "golfa sedmicu": the generation number those words stand for (only right after a model).
_GENERATION_WORDS = {
    "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8",
    "trojka": "3", "trojku": "3", "cetvorka": "4", "cetvorku": "4", "petica": "5", "peticu": "5",
    "sestica": "6", "sesticu": "6", "sedmica": "7", "sedmicu": "7", "sedmicom": "7", "osmica": "8", "osmicu": "8",
}
# Case endings of a model name ("golfa", "passatom", "octaviju", "fabije"): (ending, replacement).
_ENDINGS = (("ijom", "ia"), ("iju", "ia"), ("ije", "ia"), ("iji", "ia"), ("ovima", ""), ("ove", ""), ("om", ""),
            ("em", ""), ("om", "a"), ("u", "a"), ("e", "a"), ("i", "a"), ("a", ""), ("u", ""), ("e", ""))


def _lemma(token, previous=""):
    """"golfa" -> "golf", "octaviju" -> "octavia", "skodu" -> "skoda": a case form of a catalogue name, else the token.
    After a make ("renault astre") only that make's own models count."""
    folded = fold(token)
    make = fold(previous)
    if folded in SLANG:
        return SLANG[folded]
    if folded.startswith("oktav"):
        folded = "oc" + folded[2:]  # "oktaviju" is "octavia" spelt as it is said
    if (len(folded) < 4 or not folded.isalpha() or folded in _KNOWN_MODELS or folded in MAKE_MODELS
            or folded in GENERATIONS):
        return token
    for ending, replacement in _ENDINGS:
        if folded.endswith(ending) and len(folded) - len(ending) >= 3:
            stem = folded[:len(folded) - len(ending)] + replacement
            if make in MAKE_MODELS and stem not in MAKE_MODELS[make]:
                continue
            if stem in _KNOWN_MODELS or stem in MAKE_MODELS or stem in GENERATIONS:
                return stem
    return token


def _search_tokens(rest):
    tokens = []
    for raw in re.split(r"\s+", rest.lower().replace("'", "").replace("\u2019", "")):
        if re.search(r"[<>=(){}]", raw.strip("()[]\"'!?.,:")):
            continue  # markup or code ("<script>", "'1'='1"), never a model name; "(golf)" is still golf
        raw = re.sub(r"^([0-9])[,]([0-9][a-z]?)$", r"\1.\2", raw)  # "3,0" is the engine size 3.0, not "3 0"
        token = clean_search(raw).strip(".-")
        folded = fold(token)
        if tokens and folded in _GENERATION_WORDS and _generation_model(tokens[-1]):
            number = _GENERATION_WORDS[folded]
            if number in GENERATIONS[_generation_model(tokens[-1])]:
                tokens.append(number)  # "golf vii", "golfa sedmicu"
                continue
        mark = re.fullmatch(r"mk([0-9])", folded)
        if mark and tokens and _generation_model(tokens[-1]):
            tokens.append(mark.group(1))  # "golf mk7"
            continue
        if re.fullmatch(r"[0-9]{5,}", folded):
            continue  # "do 999999999 KM": an amount out of range is not a model number
        if (token and folded not in _STOPWORDS and fold(token.rstrip("s")) not in _STOPWORDS
                and not folded.startswith(_FILLER_PREFIXES)):
            tokens.extend(_lemma(_singular(token), tokens[-1] if tokens else "").split())
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


# The catalogue of makes and their models. Exact names are never rewritten ("Renault Austral" is not a
# misspelt "Astra"); a one-letter typo is repaired only to a model that fits the make named before it.
MAKE_MODELS = {
    "volkswagen": "golf polo passat jetta tiguan touran touareg caddy arteon scirocco sharan up amarok transporter",
    "skoda": "octavia superb fabia rapid kodiaq karoq kamiq scala yeti roomster citigo felicia favorit",
    "audi": "a1 a3 a4 a5 a6 a7 a8 q2 q3 q5 q7 q8 tt allroad",
    "bmw": "x1 x2 x3 x4 x5 x6 z3 z4 i3 i8 serija series",
    "mercedes": "klasa class sprinter vito viano citan",
    "opel": "astra corsa insignia zafira vectra mokka meriva adam agila antara combo crossland grandland omega tigra",
    "ford": "focus fiesta mondeo kuga fusion puma ecosport galaxy transit ranger",
    "renault": "megane clio scenic laguna captur kadjar austral koleos kangoo twingo talisman espace fluence arkana "
               "trafic master modus symbol",
    "peugeot": "partner rifter expert boxer bipper",
    "citroen": "berlingo picasso xsara saxo nemo jumpy jumper",
    "fiat": "punto panda stilo bravo doblo tipo freemont linea fiorino ducato brava",
    "seat": "ibiza leon toledo altea ateca arona alhambra tarraco",
    "toyota": "corolla yaris avensis auris verso aygo prius camry hilux land",
    "honda": "civic accord jazz",
    "hyundai": "tucson santa kona ioniq getz elantra accent matrix veloster",
    "kia": "ceed sportage sorento rio picanto stonic niro carens venga soul",
    "nissan": "qashqai juke micra note almera leaf navara primera terrano murano",
    "mazda": "mazda demio premacy tribute",
    "suzuki": "vitara swift jimny ignis baleno liana alto splash",
    "dacia": "duster logan sandero lodgy dokker jogger spring",
    "volvo": "v40 v50 v60 v70 v90 s40 s60 s80 s90 xc40 xc60 xc70 xc90",
    "mitsubishi": "outlander lancer pajero colt carisma",
    "subaru": "forester impreza outback legacy",
    "chevrolet": "spark cruze aveo captiva lacetti matiz",
    "tesla": "model",
    "jeep": "wrangler compass renegade cherokee grand",
    "alfa": "romeo giulia giulietta mito stelvio",
    "land": "rover discovery defender freelander evoque",
    "lancia": "ypsilon delta musa",
    "smart": "fortwo forfour",
    "mini": "cooper countryman clubman",
}
MAKE_MODELS = {make: frozenset(names.split()) for make, names in MAKE_MODELS.items()}
_KNOWN_MODELS = frozenset(name for names in MAKE_MODELS.values() for name in names)
COMMON_NAMES = tuple(dict.fromkeys(
    [name for name in list(MAKE_MODELS) + sorted(_KNOWN_MODELS) + list(GENERATIONS)
     if name.isalpha() and len(name) >= 4]))
TYPO_CUTOFF = 0.8


def repair_typo(token, previous=""):
    """"pasat" -> "passat": the one make / model word a token is a near miss of, else the token.

    Names in the catalogue are exact and never rewritten. After a make ("renault astre"), only that make's
    own models are candidates, so a real but unlisted model is left for the database to suggest."""
    folded = fold(token)
    if (len(folded) < 5 or not folded.isalpha() or folded in COMMON_NAMES or folded in _KNOWN_MODELS
            or folded in MAKE_MODELS or folded in _STOPWORDS or is_place(folded)):
        return token  # "Doboj" is a town, never a misspelt "Doblo"
    make = fold(previous)
    # "octavie", "passati", "golfovi" are inflected forms, not typos: the database search handles those
    close = [name for name in difflib.get_close_matches(folded, COMMON_NAMES, n=3, cutoff=TYPO_CUTOFF)
             if name[0] == folded[0] and not folded.startswith(name)
             and not (folded[:-1] == name[:-1] and folded[-1] in "aeiou")
             and (make not in MAKE_MODELS or name in MAKE_MODELS[make])]
    if len(close) == 1 or (close and difflib.SequenceMatcher(None, folded, close[0]).ratio()
                           - difflib.SequenceMatcher(None, folded, close[1]).ratio() > 0.05):
        return close[0]
    return token


def _is_catalog_word(token):
    """True when a token is, or is a near miss of, a make / model / generation name (or carries a digit,
    like "320d", "a4", "x5"). Anything else left over in a sentence is conversation, not a car."""
    folded = fold(token)
    if any(char.isdigit() for char in folded):
        return True
    names = set(MAKE_MODELS) | _KNOWN_MODELS | set(GENERATIONS)
    if folded in names or any(len(name) >= 4 and folded.startswith(name) for name in names):
        return True
    return len(folded) >= 4 and bool(difflib.get_close_matches(folded, COMMON_NAMES, n=1, cutoff=TYPO_CUTOFF))


UNKNOWN_WORDS_MIN_SENTENCE = 4  # words; a short message ("Kuga", "renault astre") still goes to the database


def _drop_unknown_words(tokens, original):
    """In a sentence, leftover words that match no make / model (even fuzzily) are never a model:
    "hajde uzmemo obzir", "platformi prelazi". A single word ("mg", "merc") or a short message keeps its
    words for the database search: it may be an alias or a model the catalogue lacks."""
    if len(tokens) >= 2 and len(original.split()) >= UNKNOWN_WORDS_MIN_SENTENCE and not any(
            _is_catalog_word(token) for token in tokens):
        return []
    return tokens


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


# Engine sizes and trim names ("3.0", "quattro") are not in the data as fields. They stay in the search text the
# user typed (so titles and buttons keep them) but never take part in matching the make and model.
_ENGINE_SIZE = re.compile(r"[1-9]\.[0-9]")
TRIM_WORDS = frozenset("quattro xdrive 4motion 4matic 4x4 awd gti gtd gte amg sline msport".split())


def split_trim(search):
    """"audi a6 3.0 quattro" -> ("audi a6", ("3.0", "quattro")). Unchanged when nothing, or only hints, is left."""
    words = (search or "").split()
    hints = tuple(word for word in words if _ENGINE_SIZE.fullmatch(word) or fold(word) in TRIM_WORDS)
    model = [word for word in words if word not in hints]
    if not hints or not model:
        return search, ()
    return " ".join(model), hints


def hint_patterns(hints):
    """Case-insensitive regexes that find each engine / trim hint in a listing's text."""
    return [rf"(^|[^0-9.,]){re.escape(hint[0])}[.,]{re.escape(hint[2])}([^0-9]|$)" if _ENGINE_SIZE.fullmatch(hint)
            else rf"\m{re.escape(fold(hint))}\M" for hint in hints]


_COMPARE_SPLIT = re.compile(
    r"\s*(?:\||\bvs\.?\b|\bversus\b|\bnaspram\b|\bprotiv\b|\bagainst\b|\bcompared to\b|\bor\b|\bili\b|\band\b|"
    r"\bi\b|\bwith\b|\bsa\b)\s*",
    re.I)
_CAR_WORDS = None


def _label_text(chunk):
    """A compare side as a search, with a one-letter typo repaired ("pasat" -> "passat")."""
    tokens = _search_tokens(chunk)
    return clean_search(" ".join(repair_typo(token, tokens[i - 1] if i else "") for i, token in enumerate(tokens)))


def _chunk_names_car(chunk):
    """True when a compare side names a make, model or alias ("pasat" -> Passat, "octavije" -> Octavia)."""
    global _CAR_WORDS
    if _CAR_WORDS is None:
        _CAR_WORDS = set(MAKE_MODELS) | set(_KNOWN_MODELS) | set(GENERATIONS) | {"vw", "benz", "serija", "mercedes"}
    tokens = _search_tokens(chunk)
    return any(fold(repair_typo(token, tokens[i - 1] if i else "")) in _CAR_WORDS for i, token in enumerate(tokens))


def _shared_model_pair(candidates):
    """"golf 6 ili 7" -> ("golf 6", "golf 7"): the second side is only a generation of the first side's model.

    Needs exactly two sides, the second one a lone generation number / letter that the model has in
    GENERATIONS ("golf 7 ili 6", "passat b6 ili b7"). Anything else returns None.
    """
    sides = [chunk for chunk in candidates if chunk.strip()]
    if len(sides) != 2:
        return None
    second = _search_tokens(sides[1])
    if len(second) != 1:
        return None
    tokens = _search_tokens(sides[0])
    for index, token in enumerate(tokens):
        folded = fold(token)
        joined = re.fullmatch(r"([a-z]+)([0-9])", folded)
        if joined and _generation_model(joined.group(1)):  # "golf7"
            model, number, head, tail = _generation_model(joined.group(1)), joined.group(2), tokens[:index], tokens[index + 1:]
        elif _generation_model(token) and index + 1 < len(tokens):
            model, number, head, tail = _generation_model(token), fold(tokens[index + 1]), tokens[:index], tokens[index + 2:]
        else:
            continue
        other = fold(second[0])
        if tail or number == other or number not in GENERATIONS[model] or other not in GENERATIONS[model]:
            continue
        return (clean_search(" ".join(head + [model, number])), clean_search(" ".join(head + [model, other])))
    return None


def _compare_labels(text_value):
    folded = fold(text_value)
    match = re.search(r"\b(?:compare|comparison|uporedi|poredi|usporedi|razlika izmedu)\b(.*)", folded)
    candidates = []
    if match:
        body = text_value[match.start(1):]
        candidates = _COMPARE_SPLIT.split(body)
    elif re.search(r"\bvs\.?\b|\bversus\b|\bnaspram\b|\bprotiv\b|\|", folded):
        candidates = _COMPARE_SPLIT.split(text_value)
    elif re.search(r"\b(?:better|bolji|bolje|which)\b.*\b(?:or|ili)\b", folded):
        candidates = _COMPARE_SPLIT.split(text_value)
    elif (re.search(r"\b(?:or|ili)\b", folded) and len(folded.split()) <= 10
          and not _attribute(folded, _FUELS)[1] and not _attribute(folded, _GEARBOXES)[1]):
        candidates = [chunk for part in text_value.split(",") for chunk in _COMPARE_SPLIT.split(part)]
        pair = _shared_model_pair(candidates)
        if pair:
            return pair  # "golf 6 ili 7": two generations of one model
        named = [chunk for chunk in candidates if _search_text(chunk)]
        if len(named) >= 2 and all(_chunk_names_car(chunk) for chunk in named):
            # "golf ili pasat", "golf 7 ili octavia 3 do 20000", "golf, octavia ili leon": cars on every side
            # (the first two are compared)
            labels = [_label_text(chunk) for chunk in named]
            return tuple(labels[:2])
        if re.search(r"[0-9]", folded) or len(folded.split()) > 8:
            candidates = []
        elif not text_value.strip().endswith("?"):
            # no question mark: only a clear "make model ili make model" pair, never "golf ili nesto"
            if len(candidates) != 2 or any(len(_search_text(chunk).split()) < 2 for chunk in candidates):
                candidates = []
    pair = _shared_model_pair(candidates)
    if pair:
        return pair  # "uporedi golf 6 i 7", "golf 6 vs 7"
    labels = []
    for chunk in candidates:
        label = _label_text(chunk)
        if label and _attribute(fold(label), _FUELS)[1] and not _chunk_names_car(label):
            return ()  # "dizel ili benzin": fuels, not two models
        if label:
            labels.append(label)
    return tuple(labels[:2]) if len(labels) == 2 else ()


def _days_from(folded):
    months = re.search(r"\b([1-9][0-9]?)\s*(?:mjesec\w*|mesec\w*|months?)\b", folded)
    if months:  # "zadnja 3 mjeseca": 90 days (the longest window the bot keeps)
        return max(1, min(int(months.group(1)) * 30, 90))
    match = re.search(r"\b([1-9][0-9]?)\s*(?:d\b|day|days|dan|dana)", folded)
    if not match:
        match = re.search(r"\b(?:last|past|zadnjih|zadnja|zadnje|posljednjih|posljednja)\s*([1-9][0-9]?)\b", folded)
    if match:
        return max(1, min(int(match.group(1)), 90))
    if re.search(r"\b(?:godin\w*|year)\b", folded) and re.search(r"\b(?:zadnj\w*|posljednj\w*|ov\w*|last|past|this)\b", folded):
        return 90  # "ove godine": as far back as the bot keeps
    if re.search(r"\b(?:month|mjesec\w*|mesec\w*)\b", folded):
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
    original = strip_places(strip_mentions((text_value or "")[:MAX_QUESTION]).strip())[0]
    folded = fold(original)
    if _FOLLOWUP_CUE.match(original) or re.search(_MORE, folded):
        return True
    if re.search(r"^(?:ajde\s+|hajde\s+)?(?:compare|uporedi\w*|usporedi\w*|poredi\w*)\b", folded):
        return True  # "uporedi sa octaviom": with the car just searched
    filters, rest = _extract_filters(folded, original)
    if _search_tokens(rest):
        return False
    # "do 12000", "dizel", "nešto jeftinije", "može i skuplje", "koliko košta": the last search, changed
    return bool(filters.numeric() or filters.include_old or filters.attributes() or re.search(_RELATIVE, folded)
                or re.search(_PRICE_ASK, folded))


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


# "Budžet je 30k", "imam 30k", "prvi su izvan budžeta": the user states (or restates) a price cap while
# looking at an earlier search. A message that also asks for resale, trends or a recommendation is a mixed
# message instead (see ``_mixed_question``).
_BUDGET_STATE = re.compile(
    r"\b(?:budzet(?:\s+mi)?\s+je|moj\s+budzet\s+je|(?:my\s+)?budget\s+is|"
    r"imam(?:\s+(?:oko|do|max|samo|cca))?\s+(?=[0-9])|"
    r"(?:izvan|van|preko|iznad|over|above)\s+(?:(?:mog|moje|mojeg|my|the)\s+)?(?:budzet\w*|budget))")
_KN_AMOUNT = re.compile(r"[0-9]\s*(?:(?:k|h?iljad\w*|tisuc\w*|soma)\s*)?(?:kn|kuna|kune|hrk)(?![a-z0-9])")


def _budget_correction(folded):
    return (_BUDGET_STATE.search(folded) is not None
            and not re.search(f"{_LIQUID}|{_TRENDS}|{_SUGGEST}", folded))


def is_correction(text_value):
    """True when a message corrects the previous question ("mislim da sam stavio ...", "ne, nego ...",
    "budžet je 30k a prvi su izvan budžeta")."""
    folded = fold(strip_mentions((text_value or "")[:MAX_QUESTION]))
    return _CORRECTION.search(folded) is not None or _budget_correction(folded)


# Titles the bot puts on its first line, per language. Replies are parsed back from them, so a
# follow-up works whichever language the answer was in. Kept equal to telegram_i18n (tested).
_TITLES = (
    {"trends": "Observed asking-price changes · last ", "trends_unit": "days",
     "budget": "Recently observed at or below ", "find": "Observed listings", "upto": "up to ", "from": "from ",
     "family": "for a family", "old": "incl. older", "lowkm": "low mileage", "suggest": "Data-based shortlist",
     "liquidity": "⏱ Fastest to disappear from our search", "new": "incl. new", "deals": "🏷 Good deals",
     "cheapest": "cheapest first",
     "fuel": {"diesel": "diesel", "petrol": "petrol", "hybrid": "hybrid", "electric": "electric", "lpg": "LPG"},
     "gearbox": {"automatic": "automatic", "manual": "manual"}},
    {"trends": "Zabilježene promjene tražene cijene · zadnjih ", "trends_unit": "dana",
     "budget": "Nedavno viđeno do ", "find": "Viđeni oglasi", "upto": "do ", "from": "od ",
     "family": "za porodicu", "old": "i stariji", "lowkm": "malo kilometara", "suggest": "Uža lista na osnovu podataka",
     "liquidity": "⏱ Najbrže nestaju iz naše pretrage", "new": "i novi", "deals": "🏷 Dobre prilike",
     "cheapest": "prvo najjeftiniji",
     "fuel": {"diesel": "dizel", "petrol": "benzin", "hybrid": "hibrid", "electric": "električni", "lpg": "LPG"},
     "gearbox": {"automatic": "automatik", "manual": "manuelni"}},
)
_DASH = r"\s*[–—-]\s*"


def _title_filters(body, titles):
    """Filters from the ' · '-separated pieces after an answer's title."""
    values = {"search": "", "budget": None, "year_min": None, "mileage_max": None, "family": False,
              "include_old": False, "price_min": None, "year_max": None, "fuel": "", "transmission": "",
              "new_ok": False, "mileage_min": None, "low_km": False, "cheapest": False}
    upto, since = titles["upto"], titles["from"]
    fuels = {label: value for value, label in titles["fuel"].items()}
    gearboxes = {label: value for value, label in titles["gearbox"].items()}
    for piece in body.split(" · "):
        piece = piece.strip()
        if not piece:
            continue
        price_span = re.fullmatch(rf"([0-9.]+){_DASH}([0-9.]+) KM", piece)
        km_span = re.fullmatch(rf"([0-9.]+){_DASH}([0-9.]+) km", piece)
        year_span = re.fullmatch(rf"((?:19|20)[0-9]{{2}}){_DASH}((?:19|20)[0-9]{{2}})", piece)
        one_year = re.fullmatch(r"(?:19|20)[0-9]{2}", piece)
        if piece == titles["family"]:
            values["family"] = True
        elif piece == titles["old"]:
            values["include_old"] = True
        elif piece == titles["new"]:
            values["new_ok"] = True
        elif piece == titles["lowkm"]:
            values["low_km"] = True
        elif piece == titles.get("cheapest"):
            values["cheapest"] = True
        elif km_span:
            values["mileage_min"] = _amount(km_span.group(1), None, MILEAGE_RANGE)
            values["mileage_max"] = _amount(km_span.group(2), None, MILEAGE_RANGE)
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
        elif one_year:  # "golf 15" is shown as "· 2015": that model year only
            values["year_min"] = values["year_max"] = _amount(piece, None, YEAR_RANGE)
        elif piece.startswith(upto) and piece.endswith(" KM"):
            values["budget"] = _amount(piece[len(upto):-3], None, PRICE_RANGE)
        elif piece.startswith(since) and piece.endswith(" KM"):
            values["price_min"] = _amount(piece[len(since):-3], None, PRICE_RANGE)
        elif piece.startswith(upto) and piece.endswith(" km"):
            values["mileage_max"] = _amount(piece[len(upto):-3], None, MILEAGE_RANGE)
        elif piece.startswith(since) and piece.endswith(" km"):
            values["mileage_min"] = _amount(piece[len(since):-3], None, MILEAGE_RANGE)
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
        if line.startswith(titles["deals"]):
            return Intent("deals", _title_filters(line[len(titles["deals"]):], titles), days=DEAL_DEFAULT_DAYS)
        if line.startswith(titles["liquidity"]):
            return Intent("liquidity", _title_filters(line[len(titles["liquidity"]):], titles))
    return None


_PAGE_LINE = re.compile(r"(?:Showing|Prikazujem)\s+([0-9]+)(?:–[0-9]+)?\s+(?:of|od)\s+([0-9]+)\b")


def context_from_reply(reply_text):
    """Recover the previous answer's scope from the bot message being replied to.

    The title is normally the first line; when Gemini phrased the answer, its sentences come first
    and the database answer (with its title) follows, so the first line that reads as a title wins.
    """
    lines = [line.strip() for line in (reply_text or "").strip().split("\n")[:40]]
    for line in lines:
        found = _context_from_line(line)
        if found is not None:
            if found.kind == "find":
                for other in lines:  # "Showing 6–10 of 26 ...": which page this was, for "pokaži još"
                    page = _PAGE_LINE.match(other)
                    if page:
                        return replace(found, offset=int(page.group(1)) - 1, page_total=int(page.group(2)))
            return found
    return None


_FOLLOW = (r"\b(?:follow\w*|track\w*|watch\w*|prati\w*|zaprati\w*|pracenj\w*|praceni\w*|notify|alert me|"
           r"let me know|obavij?est\w*|obavest\w*)\b|\bjavi(?:te)?\s+(?:mi|nam|kad\w*)\b")
# "više ne prati passat", "prestani pratiti golf", "unfollow golf": never an offer to follow; the follow list with
# its unfollow buttons instead.
_UNFOLLOW = (r"\b(?:ne|nemoj|vise ne|prestan\w*|stop\w*|dont|don't|do not|no longer)\s+(?:(?:vise|vise da|da|to|with)\s+)?"
             r"(?:prat\w*|zaprat\w*|follow\w*|track\w*|watch\w*)|\bunfollow\w*|\b(?:otprati|odprati|otprat|odprat)\w*")
_SHORTLIST = r"\b(?:shortlist\w*|saved (?:cars?|ads?|listings?)|sacuvan\w*|sacuva\w*|moja lista|moj spisak|my list)\b"
_STATUS = r"\b(?:status|fresh\w*|up to date|stale|collector|how old|when was|azurn\w*|kolektor)\b"
_LATEST = r"\b(?:latest|newest|what'?s new|whats new|new listings|interesting|finds|najnovij\w*|sta ima novo|novo)\b"
_TRENDS = (r"\b(?:trend\w*|price changes?|prices? (?:drop|fall|rise|change)\w*|pojeftin\w*|poskup\w*)|"
           r"\bcijen\w*.*\b(?:mijenj\w*|pad\w*|rast\w*|pal[aeio]\b|porasl\w*|skocil\w*|skocil\w*)|"
           r"\b(?:mijenj|pad|rast)\w*.*\bcijen\w*")
_LIQUID = (r"\bnajlak\w*(?:\s+\w+){0,2}\s+prod\w*|\bnajbrz\w*(?:\s+\w+){0,2}\s+prod\w*|"
           r"\b(?:brzo|lako|lakse|brze)\s+(?:se\s+)?prod\w*|\b(?:naj)?likvid\w*|\beasiest to sell\b|"
           r"\bsells? (?:the )?(?:fastest|quickest)\b|\bquickest to sell\b|\bsell(?:s)? (?:fast|quickly)\b|"
           r"\bfastest (?:to )?sell\w*\b|\bnajtrazenij\w*|\bnajpopularnij\w*|\bmost (?:wanted|popular)\b")
_GREETING = (r"^(?:hej|hey|hi|hello|ej|bok|cao|zdravo|pozdrav|selam|merhaba|yo|sta ima|kako si|dobar dan|dobro jutro|"
             r"dobro vece|dobra vecer|laku noc|good (?:morning|evening|afternoon))"
             r"(?:\s+(?:bote|bot|svima|ekipo|ekipa|garaza|there|everyone|all))?\W*$")
# Small talk the bot answers itself (a short reply plus what it can do), so it never becomes a car
# search ("kakvo je vrijeme danas?" used to search for a model called "vrijeme"). Checked only on
# messages without digits. Gemini, when configured, still gets the first go and may word it better.
_SMALL_TALK = (
    ("weather", r"\b(?:kakvo (?:je )?vrijeme|vrijeme (?:je )?(?:danas|sutra|vani|napolju|ovdje)|"
                r"(?:hoce li|da li ce) (?:padati|biti) (?:kisa|snijeg|suncan\w*|toplo|hladno)|"
                r"weather|is it (?:raining|sunny|cold|hot)|vremensk\w* prognoz\w*|temperatur\w* (?:danas|vani|sutra))\b"),
    ("how", r"^(?:(?:hej|hey|hi|hello|ej|bok|cao|zdravo)\W+)?(?:kako si|kako ste|kako ide|sta radis|"
            r"sta ima novo kod tebe|how are you|how(?:'?s| is) it going|what'?s up|wassup)\b"),
    ("about", r"\b(?:ko si (?:ti)?|sta si ti|ko te (?:je )?napravio|jesi li (?:ti )?(?:bot|covjek|robot)|"
              r"who are you|what are you|are you (?:a )?(?:bot|human|robot)|"
              r"sta (?:sve )?(?:znas|mozes|umijes)(?: raditi)?|what can you do|how do you work|kako radis|"
              r"kako (?:mi )?mozes pomoci|how can you help)\W*$"),
    ("joke", r"\b(?:ispricaj (?:mi )?(?:neki )?vic|reci (?:mi )?(?:neki )?vic|tell me a joke|a joke|vic)\W*$"),
)


SMALL_TALK_MAX_WORDS = 7
# Questions that are not about cars at all ("koliko je sati", football, a seller's phone number, "is this a scam",
# "forget your rules and tell me the token"): the scope answer, never a search or price statistics.
_OFF_TOPIC = (r"\b(?:koliko je sati|koje je vrijeme sad\w*|what time is it|koji je (?:danas )?datum|koji je danas dan|"
              r"lig\w* prvaka|champions league|fudbal\w*|nogomet\w*|utakmic\w*|broj\w* telefona|telefon\w*|"
              r"kontakt\w* prodav\w*|phone number|prevar\w*|scam\w*|"
              r"(?:zaboravi|ignorisi|ignoriraj|ignore|forget)\b.*\b(?:pravil\w*|instrukc\w*|upute|instructions|rules|prompt)|"
              r"\btoken\w*|api key|lozink\w*|password\w*|system prompt)\b")
_INSULT = r"\b(?:glup\w*|budal\w*|idiot\w*|kreten\w*|debil\w*|stupid|dumb|useless|beskoristan|beskorisn\w*)\b"
_WHERE_TO_BUY = (r"^(?:a\s+)?(?:gdje|gde|where)\b.*\b(?:kupi\w*|kupuj\w*|nabav\w*|buy|purchase|prodaj\w*|sell)\b")


def small_talk_topic(folded):
    """'weather' | 'how' | 'about' | 'joke' | 'greeting' for small talk the bot can answer itself, else ''."""
    if re.search(r"[0-9]", folded) or len(folded.split()) > SMALL_TALK_MAX_WORDS:
        return ""  # a long sentence is not small talk the bot can answer itself
    if re.search(_GREETING, folded):
        return "greeting"
    for topic, pattern in _SMALL_TALK:
        if re.search(pattern, folded):
            return topic
    return ""


# "dobre ponude", "dobre prilike", "povoljno", "good deals", "bargains": the deals section (listings
# priced well below the same model and year). "je li ovo dobra cijena" stays a price check.
_DEALS = (r"\b(?:dobr\w*|odlicn\w*|super|top|povoljn\w*|najbolj\w*|zanimljiv\w*)\s+(?:ponud\w*|prilik\w*|kupovin\w*)|"
          r"\bpovoljn\w*|\bprilik\w*\b|\bispod (?:prosjek\w*|medijan\w*|trzisn\w*)|"
          r"\b(?:good|great|best|hot|top|cheap)\s+deals\b|\bbargains?\b|\bunderpriced\b|\bdeals? (?:this|of the) week\b")
_DEAL_CHEAPEST = r"\b(?:najjeftin\w*|cheapest)\b"
DEAL_WEEK_DAYS = 7
DEAL_DEFAULT_DAYS = 14  # same window as the shortlist's "priced well below similar listings"

_YEAR_SINGLE = (
    (r"(?:from|since|after|newer than|od|iza|nakon|novij\w* od|mladj\w* od)\s*((?:19|20)[0-9]{2})\b", False),
    (r"\b((?:19|20)[0-9]{2})\s*(?:\+|or newer|or later|and newer|ili novije|i novije)", False),
    (_YEAR_CEILING, "max"),
    (r"\b((?:19|20)[0-9]{2})\b", True),
)


def _stated_years(folded):
    """(match, low, high) for the model years a message names: "2016-2019", "od 2018" (open end),
    or a bare "2018" (that year only). None when it names none."""
    span = _year_range(folded)
    if span:
        return span
    for pattern, exact in _YEAR_SINGLE:
        match = re.search(pattern, folded)
        if match and YEAR_RANGE[0] <= int(match.group(1)) <= YEAR_RANGE[1]:
            year = int(match.group(1))
            if exact == "max":
                return match, None, year
            return match, year, (year if exact else None)
    return None


SHORT_NUMBER_MAX = 30  # "golf 20" could be 2020; "golf 31" or higher is never offered as a year


def short_model_number(tokens, folded):
    """("golf", "20") -> (["golf"], 20): a bare two-digit number (00-30) right after a model word that is
    not one of that model's generations and has no unit ("golf 20k", "golf 20 godina", "golf 200 KS").
    It is ambiguous (year 2020, price 20.000 KM, mileage), so the caller asks; it is never guessed.
    A leading zero ("passat 08") can only be a year and is returned as (tokens, 2008, True).
    Returns (tokens, None, False) when there is no such pair."""
    for index in range(1, len(tokens)):
        word, number = tokens[index - 1], tokens[index]
        if not re.fullmatch(r"[0-9]{2}", number) or int(number) > SHORT_NUMBER_MAX:
            continue
        if not re.search(r"[a-z]", fold(word)) or fold(word) in _STOPWORDS:
            continue
        model = _generation_model(word)
        if model and number in GENERATIONS[model]:
            continue
        after = (r"(?![0-9]|\s*(?:k(?![a-z])|km|kn|bam|kam|maraka|hiljad\w*|iljad\w*|tisuc\w*|thousand|soma|som\b|"
                 r"god\w*|years?|eur\w*|kw|ks|ps|hp|cc|ccm|[.,]?[0-9]|%))")
        if re.search(rf"\b{re.escape(fold(word))}\s+{number}{after}", folded):
            if number.startswith("0"):
                return tokens[:index] + tokens[index + 1:], 2000 + int(number), True
            return tokens[:index] + tokens[index + 1:], int(number), False
    return tokens, None, False


# "pokaži još", "još", "more", "next page": the next page of the last search.
_MORE = (r"^(?:(?:hajde|ajde|molim te|please)\s+)?(?:(?:pokazi|prikazi|daj|show|give|load|see|vidi)(?:\s+mi)?\s+)?"
         r"(?:jos|vise|more|dalje|next|nastavi|continue|sljedec\w*|iduc\w*)"
         r"(?:\s+(?:jos|oglasa?|oglase|rezultat\w*|auta|stranic\w*|page|results?|ads|listings?|cars|ih|jedan|jednu))*\W*$")

# "koliko km ima prosječno", "average mileage of a golf 7": the typical mileage of a model, not its price.
_KM_NOUN = (r"(?<![0-9])\b(?:km|kms|kilometar\w*|kilometr\w*|kilometraz\w*|mileage|odvozen\w*|prijeden\w*|"
            r"pre(?:sa|sl)\w*|predj?(?:e|en)\w*)\b")
_AVG_ASK = (r"\b(?:prosje\w*|average|avg|typical|tipicn\w*|obicn\w*|uobicajen\w*|median\w*|medijan\w*|usually|"
            r"normaln\w*|koliko|how many|how much)\b")
_PRICE_WORD = r"\b(?:cijen\w*|price\w*|kosta\w*|cost\w*|vrijed\w*|eur|bam|kam)\b"

# Words that describe a wish, not a car ("nešto jeftino i pouzdano", "show me something nice"): the bot asks for a
# budget or a use instead of searching for a model with that name.
_VAGUE = (r"\b(?:pouzdan\w*|reliable|dependable|nice|lijep\w*|dobar auto|good car|jeftin\w*|cheap\w*|best|najbolj\w*|"
          r"beginner\w*|pocetnik\w*|pocetnic\w*|first car|prvi auto|prvo auto|udoban\w*|comfortabl\w*|ekonomic\w*|"
          r"stedljiv\w*|economical|something|nesto|sporty|sigurn\w*|safe)\b")
VAGUE_MAX_WORDS = 8
_FORECAST = r"\b(?:will|predict\w*|forecast\w*|going to|next (?:year|month|week)|prognoz\w*|hoce li|budu[cć]\w*)\b"
_CURRENCY = (r"\b(?:eur|eura|euro|eure|eurima|euros|evra|evro|evre|evri|evrima|usd|dollars?|dolar\w*)\b|[€$]")
_ACK = r"^(?:thanks|thank you|thx|ty|ok|okay|hvala|super|great|nice|cool|sjajno)\W*$"
_ACK_CORE = frozenset("thanks thank thx ty tnx ok okay okej oke hvala fala hvalaa super great nice cool sjajno odlicno "
                      "top perfect savrseno extra ekstra bravo".split())
_ACK_WORDS = _ACK_CORE | frozenset("you u e eto evo ti vam puno lijepa ljepa velika mnogo brate brat buraz bro to je "
                                   "sve that s it all much a lot ok i so very jako bas mi".split())
_ACK_EMOJI = re.compile("[\U0001F44D\U0001F64F\U0001F44C\u2764\U0001F60A\U0001F642\U0001F917\u263A\u2705]")


def is_acknowledgement(folded):
    """"hvala", "hvala puno", "ok hvala ti brate", "e super, fala", "hvala, to je to", "👍": thanks, not a question."""
    if re.search(_ACK, folded):
        return True
    words = re.findall(r"[a-z]+", folded)
    if not words:
        return bool(_ACK_EMOJI.search(folded)) and not re.search(r"[0-9]", folded)
    return (len(words) <= 7 and all(word in _ACK_WORDS for word in words) and any(word in _ACK_CORE for word in words)
            and not re.search(r"[0-9?]", folded))
_SUGGEST = (r"\b(?:suggest\w*|recommend\w*|preporuc\w*|should i buy|what to buy|what (?:car|cars) (?:should|to)|"
            r"which car|best (?:buy|value|deal|car)s?|worth buying|smartest|najpametnij\w*|pametno\w*|"
            r"sta (?:da )?kupi\w*|sta kupiti|koji auto|najbolje (?:se )?prodaj\w*|najtrazenij\w*|"
            r"best sell\w*|what sells|sells? best|popular)\b")
_ADVICE = r"\b(?:should i buy|what (?:car|to buy)|which car|recommend\w*|best (?:car|deal)s?|worth buying|preporuc\w*|koji auto|sta kupiti|sta da kupim)\b"
_WHICH_MODEL = (r"\bkoj\w*\s+(?:(?:su|je|bi|da)\s+)?(?:model\w*|auto\w*|kola|marku|marke)\b|"
                r"\bwhich\s+(?:models?|cars?)\b")


def wants_recommendation(text_value):
    """True when a message asks the bot to pick cars ("koji auto, koji modeli"), so a budget stated earlier
    is all it needs."""
    folded = fold(strip_mentions((text_value or "")[:MAX_QUESTION]))
    return re.search(f"{_SUGGEST}|{_ADVICE}|{_VAGUE}|{_WHICH_MODEL}", folded) is not None


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
# A price question without "koliko košta + year": "Audi A3 2016 cijena", "kolko je BMW 320 2014",
# "what does a 2018 BMW 320d cost?". Only without a stated price, so "Golf do 15000 cijena" stays a search.
_PRICE_ASK = (r"\bcijen\w*|\bkoli?ko\s+(?:je|kosta\w*|vrijed\w*|ide)\b|\bkoli?ko\b.*\bkosta\w*|"
              r"\bwhat\s+(?:does|do|would|will|is|are)\b.*\b(?:cost|costs|go for|sell for|worth|price)\b|"
              r"\bhow much\b|\bwhat(?:'s| is) (?:the )?(?:price|value)\b|\bprice of\b")

# "Golf sa registracijom", "registered Golf": a listing qualifier, not a procedure question. The search keeps
# the car and the answer says registration is not a filter. Without a car (or with "kako / how / gdje") it is
# a paperwork question and gets the scope message.
_REGISTRATION = r"\bregist\w*"
_PROCEDURE = (r"\b(?:kako|how|gdje|where|postupak|procedur\w*|dokument\w*|potrebn\w*|trosk\w*|kosta\w*|cost|"
              r"need\w*|treba\w*|can i|mogu li|do i)\b")


def _names_a_car(folded):
    """True when a message names a make or model from the catalogue (so it is a car search)."""
    words = set(re.findall(r"[a-z0-9]+", folded))
    return bool(words & (set(MAKE_MODELS) | _KNOWN_MODELS | set(GENERATIONS) | {"vw", "benz"}))


# Topics the bot does not cover. Motorcycles and recipes are never car questions. Finance and paperwork
# words are left alone when the message also has price / year / mileage bounds ("Golf 2015 do 9000 KM sa
# registracijom" is still a car search).
_OUT_OF_SCOPE_ALWAYS = (r"\b(?:motocikl\w*|motorcycle\w*|motorbike\w*|skuter\w*|scooter\w*|yamaha|ducati|kawasaki|"
                        r"harley\w*|aprilia|vespa|piaggio|ktm|bicikl\w*|recept\w*|recipe\w*|cevap\w*|kuhat\w*|"
                        r"skuhat\w*|kuhanj\w*|pizz\w*|burek\w*)\b")
_OUT_OF_SCOPE_UNLESS_FILTERS = (r"\b(?:kredit\w*|lizing\w*|leasing|loans?|credit|financ\w*|"
                                r"tehnick\w* pregled\w*|osiguranj\w*|insurance|porez\w*|taxes|tax|carin\w*|"
                                r"prenos vlasnistva|ownership transfer|vlasnick\w* list\w*)\b")


def _price_check(filters, folded, bare_year=True):
    """A price-check Intent: the stated amount is the price to judge, never a cap on the comparison.

    ``bare_year``: a lone year is the model year to compare (+-1). A generation ("Golf 8") or an explicit open
    bound ("od 2018") keeps its open end instead."""
    price = filters.budget or filters.price_min
    year = None
    year_min, year_max = filters.year_min, filters.year_max
    if year_min and bare_year and year_min == (year_max or year_min):
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


def _mileage_check(filters, bare_year=True):
    """A "typical mileage" Intent for a model (and its year +-1, like a price check); asks for the model if missing."""
    year = None
    year_min, year_max = filters.year_min, filters.year_max
    if year_min and bare_year and year_min == (year_max or year_min):
        year = year_min
        year_min, year_max = year - PRICE_CHECK_YEARS, year + PRICE_CHECK_YEARS
    comparison = Filters(search=filters.search, year_min=year_min, year_max=year_max, fuel=filters.fuel,
                         transmission=filters.transmission, new_ok=filters.new_ok)
    if not filters.search:
        return Intent("clarify", comparison, note="mileage_stats")
    return Intent("mileage_stats", comparison, year=year)


def _model_search(rest, named):
    """The search text: a known alias ("C klasa", "Serija 3") wins, else the remaining model words."""
    folded_rest = fold(rest)
    for pattern, canonical, _ in MODEL_ALIASES:
        if canonical and re.search(pattern, folded_rest):
            return canonical
    return canonical_search(clean_search(" ".join(named)))


CORRECTABLE = frozenset({"find", "suggest", "liquidity", "deals"})


def stated_filters(text_value, context=None):
    """The price / year / mileage bounds a message states, read the same way ``parse_question``
    reads them (including the km-as-KM rule for a correction). Used to ground Gemini's reading."""
    original = strip_mentions((text_value or "")[:MAX_QUESTION]).strip()
    folded = fold(original)
    if context is not None and context.kind in CORRECTABLE and _CORRECTION.search(folded):
        cut = _COMPLAINT.search(folded)
        if cut:
            original, folded = original[:cut.start()], folded[:cut.start()]
    return _extract_filters(folded, original)[0]


def _correct(original, folded, context):
    """Merge a correction into the previous question's filters and keep its intent, or None."""
    cut = _COMPLAINT.search(folded)
    if cut:
        original, folded = original[:cut.start()], folded[:cut.start()]
    filters, rest = _extract_filters(folded, original)
    tokens, generation = generation_hint(_search_tokens(rest))
    named = [token for token in tokens if not token.isdigit()]
    new = replace(filters, search=clean_search(" ".join(named)) if len(named) <= 3 else "")
    if generation and not (new.year_min or new.year_max):  # "pogrešno, golf 6 ne 7": that generation's years
        new = replace(new, year_min=generation[0], year_max=generation[1])
    if not (new.numeric() or new.search or new.family or new.include_old or new.attributes()):
        return None
    merged = merge_filters(context.filters, new)
    kind = context.kind
    if kind == "suggest" and not merged.budget:
        kind = "find"
    return Intent(kind, merged, note="correction")


def _correct_budget(original, folded, context):
    """"Budžet je 30k a prva dva su izvan budžeta": the previous search with the new price cap.

    Model words in the message are never read (it talks about the results, not a new model). Without an
    amount the previous cap is applied again; without either, the bot asks for the budget."""
    filters, _ = _extract_filters(folded, original)
    new = Filters(budget=filters.budget, price_min=filters.price_min, year_min=filters.year_min,
                  year_max=filters.year_max, mileage_max=filters.mileage_max, mileage_min=filters.mileage_min,
                  fuel=filters.fuel, transmission=filters.transmission)
    if not new.budget and not context.filters.budget:
        return Intent("clarify", note="over_budget")
    merged = merge_filters(context.filters, new)
    if new.budget and not new.mileage_max and context.filters.mileage_max == new.budget:
        merged = replace(merged, mileage_max=None)  # the number was read as km the first time: it is the price cap
    kind = context.kind
    if kind == "suggest" and not merged.budget:
        kind = "find"
    labels = () if new.budget else ("same_budget",)  # the answer asks for the new cap
    return Intent(kind, merged, note="correction", labels=labels)


def _more(context):
    """"pokaži još": the next page of the last find, or "no more" when it was the last page."""
    if context is None or context.kind != "find":
        return Intent("clarify", labels=("more",))
    following = max(0, context.offset) + PAGE_SIZE
    if (context.page_total is not None and following >= context.page_total) or following > MAX_PAGE_OFFSET:
        return Intent("no_more", context.filters, page_total=context.page_total)
    return Intent("find", context.filters, offset=following, note="more")


def parse_question(text_value, context=None, _restating=False):
    """Turn one question into an Intent (see ``_parse_question``). A car search that mentions registration
    carries the label "registration", so the answer can say it is not a filter."""
    text_value, place = strip_places(strip_mentions((text_value or "")[:MAX_QUESTION]))
    intent = None if _restating else _mixed_question(text_value, context)
    if intent is None:
        intent = _parse_question(text_value, context, _restating)
    if intent.kind == "unsupported" and not intent.note and not _restating:
        intent = _last_clause(text_value, context) or intent
    if (place and intent.kind in {"unsupported", "clarify"} and not intent.note and not intent.labels
            and context is not None and context.kind in CORRECTABLE):
        intent = Intent(context.kind, context.filters, days=context.days)  # "a u Sarajevu?": same search
    folded = fold(strip_mentions((text_value or "")[:MAX_QUESTION]))
    located = {"find", "suggest", "liquidity", "deals", "price_check", "model_stats", "mileage_stats", "compare"}
    if place and intent.kind in located and "location" not in intent.labels:
        intent = replace(intent, labels=intent.labels + ("location",))  # the answer says location is not a filter
    if (intent.kind in {"find", "suggest", "liquidity", "deals"} and re.search(_BODY, folded)
            and "body" not in intent.labels):
        intent = replace(intent, labels=intent.labels + ("body",))  # "SUV do 25000": body type is not a filter
    if intent.kind == "find" and "registration" not in intent.labels and re.search(_REGISTRATION, folded):
        intent = replace(intent, labels=intent.labels + ("registration",))
    if (intent.kind in {"find", "liquidity", "suggest", "deals"} and (intent.filters.budget or intent.filters.price_min)
            and "kn" not in intent.labels and _KN_AMOUNT.search(folded)):
        intent = replace(intent, labels=intent.labels + ("kn",))  # the answer says "kn" was read as KM
    return intent


def _is_keyboard_mash(folded):
    """"asdfghjkl": one long word with almost no vowels that is not close to any car name."""
    words = folded.split()
    if len(words) != 1 or not words[0].isalpha() or len(words[0]) < 6:
        return False
    word = words[0]
    if word in _STOPWORDS or re.search(_SHORTLIST + "|" + _FOLLOW + "|" + _STATUS + "|" + _LATEST, word):
        return False
    return sum(char in "aeiouy" for char in word) / len(word) < 0.2 and repair_typo(word) == word


_CHEAPER = r"\b(?:jeftinij\w*|cheaper|povoljnij\w*|manje para|nize cijen\w*|nizu cijenu|less money)\b"
_PRICIER = r"\b(?:skuplj\w*|pricier|more expensive|vise para|veci budzet|vec\w* budzet)\b"
_NEWER = r"\b(?:novij\w*|mladj\w*|newer|recent\w*)\b"
_OTHER_MODEL = (r"\b(?:drug\w*|ostal\w*|other|another|razn\w*)\s+(?:model\w*|auto\w*|kola|mark\w*|cars?|options?)\b|"
                r"\bneki drugi\b|\bnesto drugo\b|\bsta (?:jos|drugo)\b|\balternativ\w*")
_RELATIVE = "|".join((_CHEAPER, _PRICIER, _NEWER, _OTHER_MODEL, _USED))
RELATIVE_CHEAPER = 0.85  # "a jeftinije?": 15% under the cap shown, rounded down to 500 KM
RELATIVE_PRICIER = 1.2
RELATIVE_NEWER_YEARS = 2


def _round_to(value, step, up=False):
    return int((value + (step - 1 if up else 0)) // step * step)


def _relative_followup(folded, filters, context):
    """Follow-ups that move a bound of the last search instead of naming a new one: "a jeftinije?", "može i
    skuplje", "a novije?", "a neki drugi model?", "a polovni?", and a bare "koliko košta" about the car just
    searched. The model, fuel and the other bounds are kept. Without a bound to move the bot asks. None when the
    message is not such a follow-up (or names its own model / bound)."""
    if filters.search or context is None or context.kind not in CORRECTABLE:
        return None
    base = context.filters
    if re.search(_PRICE_ASK, folded) and base.search and not filters.numeric() and context.kind == "find":
        exact = bool(base.year_min and base.year_min == base.year_max)
        return _price_check(merge_filters(replace(base, budget=None, price_min=None), filters), folded, exact)
    if filters.numeric():
        return None
    if re.search(_OTHER_MODEL, folded):
        budget = filters.budget or base.budget
        if not budget:
            return Intent("clarify", note="budget")
        return Intent("suggest", replace(merge_filters(base, filters), search="", budget=budget, include_old=False))
    kind = context.kind if context.kind != "suggest" or base.budget else "find"
    if re.search(_CHEAPER, folded):
        if not base.budget:
            return Intent("clarify", note="budget")
        cap = max(PRICE_RANGE[0], _round_to(base.budget * RELATIVE_CHEAPER, 500))
        low = base.price_min if base.price_min and base.price_min < cap else None
        return Intent(kind, replace(merge_filters(base, filters), budget=cap, price_min=low), days=context.days,
                      labels=("relative",))
    if re.search(_PRICIER, folded):
        if not base.budget:
            return Intent("clarify", note="budget")
        cap = min(PRICE_RANGE[1], _round_to(base.budget * RELATIVE_PRICIER, 500, up=True))
        return Intent(kind, replace(merge_filters(base, filters), budget=cap), days=context.days, labels=("relative",))
    if re.search(_NEWER, folded):
        if not base.year_min:
            return Intent("clarify", note="newer")
        year = min(YEAR_RANGE[1], base.year_min + RELATIVE_NEWER_YEARS)
        high = base.year_max if base.year_max and base.year_max >= year else None
        return Intent(kind, replace(merge_filters(base, filters), year_min=year, year_max=high), days=context.days,
                      labels=("relative",))
    if re.search(_USED, folded) and base.new_ok:
        return Intent(kind, replace(merge_filters(base, filters), new_ok=False), days=context.days)
    return None


def _last_clause(text_value, context=None):
    """A long, rambling message whose request comes last ("..., na kraju, zanima me octavia karavan dizel do
    18000 KM"): the last clause, when it alone is a clear search. None otherwise."""
    clauses = [part for part in re.split(r"[.!?;,]+", text_value or "") if re.search(r"\w", part)]
    if len(clauses) < 2 or len((text_value or "").split()) < 9:
        return None
    intent = _parse_question(clauses[-1], context, _restating=True)
    if intent.kind in {"find", "suggest", "liquidity"} and (intent.filters.search or intent.filters.numeric()):
        return intent
    return None


_NOT_AN_INTENT = {"clarify", "unsupported", "chitchat", "ignore", "no_more"}
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\s*;\s*")
# "trendove ali i golf do 15000", "Golf vs Octavia, a onda ...", "koliko košta golf 7 i da li se brzo prodaje":
# a conjunction that starts a second request inside one sentence.
_REQUEST_BREAK = re.compile(r"\s*,?\s*\b(?:ali i|a onda|pa onda|i onda|a zatim|i zatim|i da li|i jel|i je li|"
                            r"a da li|and then|and also|but also)\b\s*", re.I)
MIXED_MAX_PARTS = 4


def _mixed_question(text_value, context=None):
    """One message that asks for several different things ("imam 30k budžet. Koje auto da kupim koje mogu
    brzo prodati. Koji su trendovi"): answer the first clear request and name what was skipped.

    A sentence that only states a budget is not a request: its filters are carried into the answer. Returns
    None unless two sentences make different requests (a single request is parsed as before)."""
    original = strip_mentions((text_value or "")[:MAX_QUESTION]).strip()
    folded = fold(original)
    if re.search(_CORRECTION, folded) or re.search(_MORE, folded):
        return None
    parts = [piece.strip() for part in _SENTENCE_BREAK.split(original) for piece in _REQUEST_BREAK.split(part)
             if re.search(r"\w", piece)]
    if not 2 <= len(parts) <= MIXED_MAX_PARTS:
        return None
    states, requests = [], []
    for part in parts:
        intent = _parse_question(part, context)
        if intent.kind in _NOT_AN_INTENT:
            continue
        if intent.kind in {"find", "suggest"} and not intent.filters.search and _BUDGET_STATE.search(fold(part)):
            states.append(intent.filters)
        else:
            requests.append(intent)
    kinds = []
    for request in requests:
        if request.kind not in kinds:
            kinds.append(request.kind)
    if len(kinds) < 2:
        return None
    chosen = requests[0]
    filters = chosen.filters
    if chosen.kind in {"find", "liquidity", "suggest", "deals"}:
        for state in states:
            filters = merge_filters(state, filters)
    skipped = tuple("skipped:" + kind for kind in kinds[1:])
    return replace(chosen, filters=filters, labels=chosen.labels + skipped)


def _shared_compare_filters(folded, original):
    """The filters a compare message applies to both sides, and the text left to read the two sides from.

    "golf 6 ili 7 do 12000" -> (budget 12000, "golf 6 ili 7"). The same unit rules as a search apply
    (unitless "do 12000" is a price, lower-case km mileage, KM currency). Two different fuels or a gearbox left
    in the text ("golf dizel ili passat benzin") are never shared: the text is then returned unchanged."""
    filters, rest = _extract_filters(folded, original)
    filters = replace(filters, search="", family=False, include_old=False, cheapest=False, low_km=False)
    if not (filters.numeric() or filters.attributes()):
        return Filters(), original
    if _attribute(fold(rest), _FUELS)[1] or _attribute(fold(rest), _GEARBOXES)[1]:
        return replace(filters, fuel="", transmission=""), original
    return filters, rest


_CONSIDER = re.compile(r"\b(?:uzm\w+\s+u\s+obzir|u obzir|razmot\w+|razmatr\w+|consider\w*)\b")


def _parse_question(text_value, context=None, _restating=False):
    """Turn one question into an Intent. ``context`` is an Intent from a replied-to answer
    (or, for a correction, the same person's previous question)."""
    original = strip_mentions((text_value or "")[:MAX_QUESTION]).strip()
    original = re.sub(r"^/ask(?:@\w+)?\s*", "", original, flags=re.I).strip()
    if not original:
        return Intent("unsupported")
    folded = fold(original)

    if is_acknowledgement(folded):
        return Intent("ignore")
    topic = small_talk_topic(folded)
    if topic:
        return Intent("chitchat", labels=(topic,))
    if re.search(_MORE, folded):
        return _more(context)
    if re.search(_OFF_TOPIC, folded):
        return Intent("chitchat", labels=("scope",))
    if not _names_a_car(folded) and not re.search(r"[0-9]", folded):
        if re.search(_INSULT, folded):
            return Intent("chitchat")
        if re.search(_WHERE_TO_BUY, folded):
            return Intent("chitchat", labels=("scope",))  # "gdje da kupim polovan auto": general advice
        if _is_keyboard_mash(folded):
            return Intent("unsupported")
    if re.search(_UNFOLLOW, folded):
        return Intent("following")  # the follow list, with an unfollow button per model
    if re.search(_OUT_OF_SCOPE_ALWAYS, folded):
        return Intent("chitchat", labels=("scope",))
    if re.search(_OUT_OF_SCOPE_UNLESS_FILTERS, folded):
        bounded = _extract_filters(folded, original)[0]
        if not (bounded.numeric() or bounded.attributes()):
            return Intent("chitchat", labels=("scope",))
    registration = re.search(_REGISTRATION, folded) is not None
    if registration and (re.search(_PROCEDURE, folded) or not _names_a_car(folded)):
        return Intent("chitchat", labels=("scope",))  # "kako da registrujem auto": a procedure, not a listing

    if (not _restating and context is not None and context.kind in CORRECTABLE
            and not _CORRECTION.search(folded) and _budget_correction(folded)):
        return _correct_budget(original, folded, context)
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
            said = _CORRECTION.search(folded)
            tail = original[said.end():] if said else ""
            if re.search(r"\w", tail):  # "... pa sam mislio golf ili tako nešto do 15 hiljada": what follows the phrase
                restated = _parse_question(tail, _restating=True)
                if restated.kind in {"find", "suggest", "liquidity"} and (
                        restated.filters.search or restated.filters.budget or restated.filters.price_min):
                    return restated
            restated = _parse_question(original, _restating=True) if original.strip() else Intent("unsupported")
            if restated.kind in {"find", "suggest", "liquidity"} and (
                    restated.filters.search or restated.filters.budget or restated.filters.price_min):
                return restated
            return Intent("clarify", note="correction")
    if re.search(_FORECAST, folded):
        return Intent("unsupported", note="forecast")
    if re.search(_CURRENCY, folded):
        return Intent("clarify", note="currency")

    years = _stated_years(folded)
    bare_year = bool(years and years[2] is not None and years[2] == years[1])  # "2018", not "od 2018" / a range
    shared, compare_text = _shared_compare_filters(folded, original)
    if years:
        match, low, high = years
        yearless = original[:match.start()] + " " * (match.end() - match.start()) + original[match.end():]
        labels = _compare_labels(yearless)
        if labels:  # "Octavia ili Passat 2016-2019?": both cohorts in the years the user gave
            return Intent("compare", replace(shared, year_min=low, year_max=high, family=wants_family(folded)),
                          labels=labels)
    labels = _compare_labels(compare_text)
    if not labels and compare_text is not original:
        labels = _compare_labels(original)
        shared = replace(shared, fuel="", transmission="")
    if labels:  # shared filters ("do 12000", "dizel", "od 2015") apply to both sides
        return Intent("compare", replace(shared, family=wants_family(folded)), labels=labels)
    asked = re.search(r"^(?:a\s+)?(?:ajde\s+|hajde\s+)?(?:compare|uporedi\w*|usporedi\w*|poredi\w*)\b(.*)$", folded)
    if asked:  # "uporedi sa octaviom" after a Passat search; "uporedi ih" with nothing to go on
        other = _search_text(original[asked.start(1):])
        if other and context is not None and context.filters.search and fold(context.filters.search) != fold(other):
            return Intent("compare", labels=(context.filters.search, other))
        if len(other.split()) <= 1:
            return Intent("clarify", note="compare")

    if re.search(_TRENDS, folded):
        days = _days_from(folded)
        if days == 7 and context and context.kind == "trends" and not re.search(r"\b(?:week|sedmic)", folded):
            return Intent("trends", days=context.days)
        return Intent("trends", days=days)

    filters, rest = _extract_filters(folded, original)
    unitless = _UNITLESS[0]
    tokens = _search_tokens(rest)
    tokens, generation = generation_hint(tokens)
    tokens = [repair_typo(token, tokens[i - 1] if i else "") for i, token in enumerate(tokens)]
    tokens = _drop_unknown_words(tokens, original)
    if not (generation or filters.year_min or filters.year_max):
        tokens, short_number, is_year = short_model_number(tokens, folded)
        if short_number and is_year:  # "passat 08": only a year can start with 0
            filters = replace(filters, year_min=short_number, year_max=short_number)
        elif short_number:  # "golf 20": a year, a price or a mileage? Ask, never guess.
            named = tokens if any(not token.isdigit() for token in tokens) else []
            return Intent("clarify", replace(filters, search=_model_search(rest, named)), note="short_number",
                          offset=short_number)
    named = tokens if any(not token.isdigit() for token in tokens) else []
    filters = replace(filters, search=_model_search(rest, named))
    if generation and not (filters.year_min or filters.year_max):
        filters = replace(filters, year_min=generation[0], year_max=generation[1])

    if (re.search(_KM_NOUN, folded) and re.search(_AVG_ASK, folded) and not re.search(_PRICE_WORD, folded)
            and not (filters.mileage_max or filters.mileage_min or filters.low_km or filters.budget or filters.price_min)
            and not re.search(_LIQUID, folded)):
        return _mileage_check(filters, bare_year)

    if re.search(_DEALS, folded) and not re.search(_LIQUID, folded):
        if re.search(r"\b(?:je li|da li|jel|is (?:this|that|it))\b", folded):
            return _price_check(filters, folded, bare_year)  # "je li ovo dobra ponuda: Golf 2017 15000 KM"
        week = re.search(r"\b(?:ov[aeiu]|ovaj|this)\s+(?:sedmic\w*|tjed\w*|week)|\b(?:danas|today)\b", folded)
        return Intent("deals", replace(filters, include_old=False),
                      days=DEAL_WEEK_DAYS if week else DEAL_DEFAULT_DAYS,
                      labels=("cheapest",) if re.search(_DEAL_CHEAPEST, folded) else ())

    if re.search(_PRICE_CHECK, folded) and not re.search(_LIQUID, folded):
        return _price_check(filters, folded, bare_year)

    if (re.search(_PRICE_ASK, folded) and filters.search and not filters.family
            and not (filters.budget or filters.price_min) and not re.search(_LIQUID, folded)):
        return _price_check(filters, folded, bare_year)  # "Audi A3 2016 cijena": that model year +-1, not "from 2016"

    if (unitless and filters.budget == unitless and unitless > BARE_KM_ASK_MAX and filters.search
            and filters.mileage_max is None and not re.search(_LIQUID, folded)):
        # "golf do 150 000" with no unit: no Golf costs 150.000 KM, but it could. Ask, never guess.
        return Intent("clarify", replace(filters, budget=None), note="km_unit", offset=unitless)

    if re.search(_LIQUID, folded):
        return Intent("liquidity", filters)

    if re.search(_SHORTLIST, folded) and not filters.search and filters.budget is None:
        return Intent("shortlist")

    if re.search(_FOLLOW, folded):
        generic = {"model", "modeli", "modela", "models", "cars", "auta", "automobili"}
        named = " ".join(word for word in filters.search.split() if fold(word) not in generic)
        if named:  # "prati golf 7", "zaprati mi passat do 15000": the model is followed (bounds are not part of it)
            return Intent("follow", Filters(search=named))
        return Intent("following")

    relative = _relative_followup(folded, filters, context)
    if relative is not None:
        return relative

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

    if (re.search(_VAGUE, folded) and len(original.split()) <= VAGUE_MAX_WORDS and not _names_a_car(folded)
            and not filters.numeric() and not filters.attributes() and not filters.family
            and not filters.include_old):
        return Intent("clarify", labels=("vague",))  # asks for a budget or a use; Gemini may still word it

    if context and context.kind == "trends" and tokens and all(t.isdigit() for t in tokens):
        return Intent("trends", days=max(1, min(int(tokens[0]), 90)))

    if len(tokens) > 4 or (not (filters.numeric() or (filters.attributes() and not tokens)) and len(original.split()) >= 9):
        return Intent("unsupported")  # a sentence, not a car name

    if filters.search and re.search(_DEAL_CHEAPEST, folded):
        filters = replace(filters, cheapest=True)  # "koji je najjeftiniji golf": golfs, cheapest first

    if context and context.kind == "find" and (not filters.is_empty() or filters.include_old):
        return Intent("find", merge_filters(context.filters, filters))

    if (context and context.kind in {"suggest", "liquidity", "deals"} and (filters.numeric() or filters.attributes())
            and not filters.search
            and (context.kind != "suggest" or context.filters.budget or filters.budget)):
        # "a od 2020?" in reply to a shortlist: same question, narrower bounds
        return Intent(context.kind, merge_filters(context.filters, filters),
                      days=context.days if context.kind == "deals" else 7)

    if (not context and filters.budget and not filters.search and not filters.family and _CONSIDER.search(folded)
            and not _names_a_car(folded)):
        # "uzmimo u obzir od 20.000 do 30.000 KM" with no earlier question to refine: the most listed models
        return Intent("suggest", replace(filters, include_old=False))

    if not filters.is_empty():
        if (filters.search and not filters.family and not filters.numeric() and re.search(_STATS, folded)):
            return Intent("model_stats", filters)
        return Intent("find", filters)

    return Intent("unsupported")
