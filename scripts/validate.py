"""Validerar veckofiler och kursfiler mot schema och mot projektets referensdata.

Utöver JSON Schema-kontrollen görs korskontroller som schemat inte kan uttrycka:
- veckonummer stämmer med filnamn och period (ISO-vecka, mån–sön)
- sektorer finns i data/sectors.json
- varje "matchad" ticker finns i data/instruments.csv med samma yahoo_ticker/börs
  (skydd mot påhittade tickers)
- inget bolag markerat "ej i listan" finns i instruments.csv (missad matchning)
- ett indirekt kopplat bolag tillhör (enligt kolumnen sektor) nyhetens sektor
  eller en sektor i nyhetens sector_impacts
- makrosammanfattningen säger inte att styrräntan sänkts/höjts om nyckeltalets
  ändringsdatum ligger före veckan
- sentiment per nyhet: basis pekar på befintliga citat; motiveringens siffror och
  riktning finns i just de citaten; minst ett gemensamt innehållsord; inga ord om
  kurs eller råd; rutinmeddelanden är neutrala (kontrollerar spårbarhet, inte
  om bedömningen är riktig)
- publiceringsdatum ligger inom veckan
- varje siffra i rubrik/sammanfattning finns exakt (efter normalisering, se
  extract_numbers) i något evidence-citat – avrundade siffror underkänns
- riktningsord före ett tal (ökade/sjönk m.fl.) motsäger inte citatets tecken
  eller riktningsord (direction_conflicts)
- evidence-citat är högst 25 ord
- source.url är samma som rådatans index anger för samma raw_file
- varje evidence-citat finns ordagrant i källtexten under data/raw/<vecka>/
  (hoppas över med varning om rådata saknas, t.ex. i CI – data/raw/ committas inte)
- filer under data/mock/ har "mock": true och valideras mot data/mock/instruments.csv;
  filer utanför data/mock/ får inte vara mock (så att exempeldata aldrig publiceras)

Användning:
    python scripts/validate.py                 # alla filer i data/weeks, data/prices, data/mock
    python scripts/validate.py data/weeks/2026-W40.json
"""

from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workdays import business_days_between  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
MOCK_DIR = DATA_DIR / "mock"

MAX_QUOTE_WORDS = 25

# Källänken för styrräntan (samma som fetch.RIKSBANK_PAGE_URL – ett test håller dem i synk).
RIKSBANK_PAGE_URL = "https://www.riksbank.se/sv/statistik/rantor-och-valutakurser/styrranta-in--och-utlaningsranta/"

# Hårt mellanslag, smalt hårt mellanslag, tunt mellanslag och siffermellanslag
# behandlas som vanligt mellanslag (vanliga tusentalsavgränsare i svensk text).
_SPACE_CHARS = str.maketrans({c: " " for c in "    "})

# Ett tal: valfritt minustecken (bindestreck, tankstreck eller typografiskt minus)
# som bara räknas om det inte står direkt efter ett ord eller en siffra – så att
# "2026–2027" blir två positiva tal. Heltalsdelen kan ha mellanslag som
# tusentalsavgränsare ("12 500"), decimaldelen komma eller punkt. Efterföljande
# "%" eller "procent" (men inte "procentenheter") markerar procenttal.
_NUMBER_RE = re.compile(
    r"(?<![\w.,])(?P<sign>[-–−](?=\d))?"
    r"(?<![\w.,])(?P<int>\d{1,3}(?: \d{3})+(?!\d)|\d+)"
    r"(?:(?P<sep>[.,:])(?P<frac>\d+))?"
    r"(?P<pct> ?(?:%|procent(?!enhet)))?"
)
# Engelska texter (språket "en" i rådatans index): komma är tusentalsavgränsare och punkt decimaltecken
# ("US$1,234.5 million" -> 1234,5; Lundin Gold och Lundin Mining 2026-11-05). Ett komma som inte följs av
# exakt tre siffror är ett skiljetecken.
_NUMBER_RE_EN = re.compile(
    r"(?<![\w.,])(?P<sign>[-–−](?=\d))?"
    r"(?<![\w.,])(?P<int>\d{1,3}(?:,\d{3})+(?!\d)|\d+)"
    r"(?:(?P<sep>[.:])(?P<frac>\d+))?"
    r"(?P<pct> ?(?:%|percent\b|procent(?!enhet)))?"
)
# Två skrivsätt i bolagens rapporter som tolkas särskilt, men bara i ett belopp eller värde per aktie:
# en valutaenhet står efter talet, eller talet står inom parentes direkt efter ett tal med samma
# skrivsätt ("2.500 MSEK (2.342)", "0:21 SEK (1:26)"). Annars tolkas de som förut.
# - "2.500": punkt som tusentalsavgränsare (exakt tre siffror efter punkten) -> 2 500 (Nolato 2026-10-29)
# - "0:21": kolon som decimaltecken (exakt två siffror efter kolon) -> 0,21 (Xano 2026-10-29); utan
#   enhet efter är "08:00" ett klockslag och blir två tal som förut
_AMOUNT_UNIT_RE = re.compile(
    r"\s?(?:M?D?(?:SEK|EUR|USD|NOK|DKK)\b|T(?:SEK|EUR)\b|[Mm]d?kr\b|tkr\b|kr\b|kronor\b|miljoner\b|miljarder\b|"
    r"[Mm]dr\b|Euro cent\b|öre\b)"
)


# Riktningsord matchas på ordets början, så att böjningar räknas ("sänks",
# "sänktes", "sänkning"). Listan fångar inte allt – se README.
_UP_STEMS = ("ök", "steg", "stig", "höj", "väx", "stärk")
_DOWN_STEMS = ("minsk", "sjönk", "sjunk", "föll", "fall", "sänk", "försvag")
UP, DOWN = "upp", "ned"

# Hur många ord före talet som söks efter ett riktningsord. Sökningen stannar
# också vid föregående tal och vid meningsslut.
_DIRECTION_WINDOW = 4
_SENTENCE_END_RE = re.compile(r"[.!?;:]\s")
_WORD_RE = re.compile(r"[^\W\d_]+")


def _direction_before(window: str) -> str | None:
    """Närmaste riktningsord i window (texten före ett tal), eller None."""
    ends = list(_SENTENCE_END_RE.finditer(window))
    if ends:
        window = window[ends[-1].end():]
    for word in reversed(_WORD_RE.findall(window)[-_DIRECTION_WINDOW:]):
        w = word.casefold()
        if w.startswith(_UP_STEMS):
            return UP
        if w.startswith(_DOWN_STEMS):
            return DOWN
    return None


@dataclass(frozen=True)
class Number:
    """Ett normaliserat tal ur en text. raw behålls för felmeddelanden.

    direction är riktningsordet som står närmast före talet (UP/DOWN/None).
    """

    value: Decimal
    percent: bool
    raw: str
    direction: str | None = None
    level: bool = False  # "till" direkt före talet: en nivå, inte en förändring

    @property
    def sign(self) -> str | None:
        """Talets riktning i ett citat: minustecken eller minskningsord ger DOWN,
        ökningsord eller ett positivt tal utan riktningsord ger UP."""
        if self.value < 0 or self.direction == DOWN:
            return DOWN
        if self.direction == UP or self.value > 0:
            return UP
        return None

    def supported_by(self, other: "Number") -> bool:
        """Sant om citatets tal belägger detta tal.

        Värdet måste vara exakt lika (1,50 == 1,5 men 2,1 != 2,13). Ett positivt
        tal i sammanfattningen får beläggas av motsvarande negativa ("minskade 3,2
        procent" ur "−3,2 %"). Procent i sammanfattningen kräver procent i citatet,
        men sammanfattningen får utelämna enheten.
        """
        value_ok = other.value == self.value or (self.value > 0 and other.value == -self.value)
        unit_ok = other.percent or not self.percent
        return value_ok and unit_ok


def _special_style(text: str, m: re.Match, prev_style: str | None, prev_end: int) -> str | None:
    """"thousands" ("2.500") eller "colon" ("0:21") om talet ska tolkas så (se _AMOUNT_UNIT_RE), annars None."""
    sep, frac = m["sep"], m["frac"]
    if sep == "." and frac and len(frac) == 3 and " " not in m["int"] and len(m["int"]) <= 3 and not m["pct"]:
        style = "thousands"
    elif sep == ":" and frac and len(frac) == 2 and not m["pct"]:
        style = "colon"
    else:
        return None
    if _AMOUNT_UNIT_RE.match(text, m.end()):
        return style
    # Inom parentes direkt efter ett tal med samma skrivsätt: "2.500 MSEK (2.342)".
    in_parens = text[:m.start()].rstrip().endswith("(") and text[m.end():].lstrip().startswith(")")
    if in_parens and prev_style == style and "(" in text[prev_end:m.start()]:
        return style
    return None


def extract_numbers(text: str, language: str | None = None) -> list[Number]:
    """Plockar ut alla tal ur texten efter normalisering av mellanslag, decimaltecken och minus.
    Används både av veckosammanfattningens och rapportutfallens validering (validate_results).
    language "en": engelskt skrivsätt (komma som tusentalsavgränsare, punkt som decimaltecken, se
    _NUMBER_RE_EN); annars svenskt, med de särskilda skrivsätten i _special_style."""
    english = language == "en"
    text = text.translate(_SPACE_CHARS)
    out = []
    prev_end = 0
    prev_style: str | None = None
    for m in (_NUMBER_RE_EN if english else _NUMBER_RE).finditer(text):
        style = None if english else _special_style(text, m, prev_style, prev_end)
        before = text[prev_end:m.start()]
        words = _WORD_RE.findall(before)
        level = bool(words) and words[-1].casefold() == "till" and before.rstrip().casefold().endswith("till")
        sign = -1 if m["sign"] else 1
        if style == "thousands":
            values = [Decimal(m["int"] + m["frac"])]
        elif style is None and m["sep"] == ":":
            # Klockslag och liknande ("08:00"): två tal, som innan kolon kunde vara ett skiljetecken i talet.
            values = [Decimal(m["int"].replace(" ", "")), Decimal(m["frac"])]
        else:
            values = [Decimal(m["int"].replace(" ", "").replace(",", "") + ("." + m["frac"] if m["frac"] else ""))]
        for i, value in enumerate(values):
            out.append(Number(
                value=sign * value if i == 0 else value,
                percent=bool(m["pct"]),
                raw=m.group(0).strip(),
                direction=_direction_before(before),
                level=level,
            ))
        prev_style = style
        prev_end = m.end()
    return out


def unsupported_numbers(text: str, quotes: list[str], quote_language: str | None = None) -> list[str]:
    """Returnerar talen i text som inte beläggs exakt av något tal i quotes.
    quote_language: citatens språk (rapportens); texten är alltid svensk."""
    supporting = [n for q in quotes for n in extract_numbers(q, quote_language)]
    return [n.raw for n in extract_numbers(text) if not any(n.supported_by(s) for s in supporting)]


def direction_conflicts(text: str, quotes: list[str], quote_language: str | None = None) -> list[str]:
    """Returnerar talen i text vars riktningsord motsäger citatet.

    Ett ökningsord före talet kräver att något belägg i citaten går uppåt, och ett
    minskningsord kräver att något belägg går nedåt (se Number.sign). Tal utan
    riktningsord, nivåer ("sänktes till 1,75") och tal som saknar belägg helt
    (fångas av unsupported_numbers) kontrolleras inte här. "med" kontrolleras.
    """
    supporting = [n for q in quotes for n in extract_numbers(q, quote_language)]
    out = []
    for n in extract_numbers(text):
        if n.direction is None or n.level:
            continue
        candidates = [s for s in supporting if n.supported_by(s)]
        if candidates and not any(s.sign == n.direction for s in candidates):
            word = "ökningsord" if n.direction == UP else "minskningsord"
            out.append(f"{n.raw} ({word}, citatet går åt andra hållet)")
    return out


@dataclass
class Result:
    """Fel stoppar publicering; varningar skrivs bara ut."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


INSTRUMENT_TYPES = {"investmentbolag"}
MFN_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Cisions slugar kan ha dubbla och avslutande bindestreck ("meren-energy-inc-", "l-e-lundbergforetagen-ab--publ-").
CISION_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


@dataclass(frozen=True)
class Instrument:
    namn: str
    alias: tuple[str, ...]
    ticker: str
    yahoo_ticker: str
    bors: str
    mfn_slug: str | None = None  # bolagets slug i MFN:s URL:er (mfn.se/a/<slug>/…), valfri
    sektor: str | None = None  # id ur data/sectors.json, valfri
    isin: tuple[str, ...] = ()  # alla bolagets aktieslag, valfri (används för FI-registren)
    typ: str | None = None  # bolagstyp, valfri: "investmentbolag" (P/E visas inte i "Kurs mot vinst")
    # Analytikerlänkar i kursreaktionen, valfria och kontrollerade innan de läggs in (ingen hämtning sker):
    placera_tagg: str | None = None  # Placeras taggsida placera.se/taggar/<slug> (finns i Placeras tagg-sitemap)
    afv_aktie: str | None = None  # Affärsvärldens bolagssida affarsvarlden.se/aktie/<slug> (sidan har bolagets ISIN)
    # Bolagets eget nyhetsrum hos Cision, news.cision.com/<slug> (valfri): för bolag som inte syns i Cisions svenska
    # lista. fetch.py hämtar då news.cision.com/<slug>/ListItems?format=rss (sidans egen RSS-ikon).
    cision_slug: str | None = None

    def analyst_links(self) -> dict:
        """Länkarna för kolumnen "Analytiker" i kursreaktionen."""
        return {
            "placera": f"https://www.placera.se/taggar/{self.placera_tagg}" if self.placera_tagg else None,
            "affarsvarlden": f"https://www.affarsvarlden.se/aktie/{self.afv_aktie}" if self.afv_aktie else None,
        }


def load_instruments(
    path: Path = DATA_DIR / "instruments.csv", sector_ids: set[str] | None = None
) -> dict[str, Instrument]:
    """Läser instruments.csv och returnerar instrumenten nycklade på ticker.

    Kolumnerna mfn_slug och sektor är valfria (både kolumnen och värdet).
    mfn_slug prioriterar MFN-poster i urvalet och sektor styr vilka bolag som
    får kopplas indirekt till en nyhet, så felaktiga värden stoppas här.
    sector_ids: giltiga sektorer (standard: data/sectors.json).
    utf-8-sig så att filen fungerar även om den sparats från Excel (BOM).
    """
    if sector_ids is None:
        sector_ids = load_sector_ids()
    instruments: dict[str, Instrument] = {}
    slugs: dict[str, str] = {}
    cision_slugs: dict[str, str] = {}
    name_owner: dict[str, str] = {}  # namn/alias (gemener) -> ticker
    isin_owner: dict[str, str] = {}  # ISIN -> ticker
    with path.open(encoding="utf-8-sig", newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            alias = tuple(a.strip() for a in (row.get("alias") or "").split("|") if a.strip())
            slug = (row.get("mfn_slug") or "").strip() or None
            sektor = (row.get("sektor") or "").strip() or None
            isins = tuple(i.strip() for i in (row.get("isin") or "").split("|") if i.strip())
            typ = (row.get("typ") or "").strip() or None
            links = {k: (row.get(k) or "").strip() or None for k in ("placera_tagg", "afv_aktie")}
            cision_slug = (row.get("cision_slug") or "").strip() or None
            if cision_slug is not None:
                if not CISION_SLUG_RE.match(cision_slug):
                    raise ValueError(f"{path.name} rad {line}: ogiltig cision_slug {cision_slug!r} (gemener, siffror och bindestreck)")
                if cision_slug in cision_slugs:
                    raise ValueError(f"{path.name} rad {line}: cision_slug {cision_slug!r} används redan av {cision_slugs[cision_slug]!r}")
                cision_slugs[cision_slug] = row["ticker"].strip()
            for k, v in links.items():
                if v is not None and not MFN_SLUG_RE.match(v):
                    raise ValueError(f"{path.name} rad {line}: ogiltig {k} {v!r} (gemener, siffror och bindestreck)")
            if typ is not None and typ not in INSTRUMENT_TYPES:
                raise ValueError(f"{path.name} rad {line}: okänd typ {typ!r} (tillåtna: {sorted(INSTRUMENT_TYPES)})")
            for isin in isins:
                if not ISIN_RE.match(isin):
                    raise ValueError(f"{path.name} rad {line}: ogiltig ISIN {isin!r}")
                owner = isin_owner.setdefault(isin, row["ticker"].strip())
                if owner != row["ticker"].strip():
                    raise ValueError(f"{path.name} rad {line}: ISIN {isin!r} används redan av {owner!r}")
            inst = Instrument(
                namn=row["namn"].strip(),
                alias=alias,
                ticker=row["ticker"].strip(),
                yahoo_ticker=row["yahoo_ticker"].strip(),
                bors=row["börs"].strip(),
                mfn_slug=slug,
                sektor=sektor,
                isin=isins,
                typ=typ,
                **links,
                cision_slug=cision_slug,
            )
            if inst.ticker in instruments:
                raise ValueError(f"{path.name} rad {line}: dubblett av ticker {inst.ticker!r}")
            if sektor is not None and sektor not in sector_ids:
                raise ValueError(f"{path.name} rad {line}: okänd sektor {sektor!r} (se data/sectors.json)")
            # Matchningen bygger på att ett namn pekar ut exakt ett instrument.
            for n in (inst.namn, *inst.alias):
                owner = name_owner.setdefault(n.casefold(), inst.ticker)
                if owner != inst.ticker:
                    raise ValueError(f"{path.name} rad {line}: namnet/aliaset {n!r} används redan av {owner!r}")
            if slug is not None:
                if not MFN_SLUG_RE.match(slug):
                    raise ValueError(f"{path.name} rad {line}: ogiltig mfn_slug {slug!r} (gemener, siffror och bindestreck)")
                if slug in slugs:
                    raise ValueError(f"{path.name} rad {line}: mfn_slug {slug!r} används redan av {slugs[slug]!r}")
                slugs[slug] = inst.ticker
            instruments[inst.ticker] = inst
    return instruments


def load_sector_ids(path: Path = DATA_DIR / "sectors.json") -> set[str]:
    return {s["id"] for s in json.loads(path.read_text(encoding="utf-8"))}


KPI_KINDS = {"belopp", "procent", "per_aktie", "volym", "pris"}


def load_sector_kpis(path: Path = DATA_DIR / "sectors.json") -> dict[str, list[dict]]:
    """Nyckeltalen att bevaka per sektor ({sektor: [{id, name, kind}]}), i prioritetsordning.

    Används av /rapportutfall och valideringen av rapportutfall. Kastar ValueError om ett
    nyckeltal saknar id/namn, har okänd typ eller förekommer två gånger i samma sektor.
    """
    out: dict[str, list[dict]] = {}
    for s in json.loads(path.read_text(encoding="utf-8")):
        kpis = s.get("kpis", [])
        ids = [k.get("id") for k in kpis]
        if len(set(ids)) != len(ids):
            raise ValueError(f"sectors.json: dubblett bland nyckeltalen i {s['id']}")
        for k in kpis:
            if not k.get("id") or not k.get("name") or k.get("kind") not in KPI_KINDS:
                raise ValueError(f"sectors.json: ogiltigt nyckeltal i {s['id']}: {k}")
        out[s["id"]] = kpis
    return out


def _schema_validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _schema_errors(validator: Draft202012Validator, data: object) -> list[str]:
    out = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(rot)"
        out.append(f"schema: {where}: {err.message}")
    return out


def _check_datetime(value: str, label: str, res: Result) -> None:
    """jsonschemas date-time-format kräver ett extrapaket – kontrollera själv i stället."""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        res.errors.append(f"{label}: ogiltig tidsstämpel {value!r}")
        return
    if dt.tzinfo is None:
        res.errors.append(f"{label}: tidsstämpeln {value!r} saknar tidszon")


def iso_week_period(week: str) -> tuple[date, date]:
    """'2026-W40' -> (måndag, söndag)."""
    year, wk = int(week[:4]), int(week[6:])
    start = date.fromisocalendar(year, wk, 1)
    return start, start + timedelta(days=6)


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _name_index(instruments: dict[str, Instrument]) -> dict[str, str]:
    """Gemener av namn och alias -> ticker, för att hitta missade matchningar."""
    index: dict[str, str] = {}
    for inst in instruments.values():
        for n in (inst.namn, *inst.alias):
            index[n.casefold()] = inst.ticker
    return index


def validate_week(
    data: dict,
    expected_week: str | None,
    instruments: dict[str, Instrument],
    sector_ids: set[str],
    raw_dir: Path | None = RAW_DIR,
) -> Result:
    """Validerar en veckofil. expected_week = filnamnets stam (None hoppar över kontrollen)."""
    res = Result()
    res.errors += _schema_errors(_schema_validator("week.schema.json"), data)
    if res.errors:
        # Korskontrollerna förutsätter rätt struktur.
        return res

    week = data["week"]
    is_mock = data.get("mock", False)
    if expected_week is not None and week != expected_week:
        res.errors.append(f"week {week!r} matchar inte filnamnet {expected_week!r}")
    _check_datetime(data["generated_at"], "generated_at", res)

    start, end = iso_week_period(week)
    if data["period"] != {"start": start.isoformat(), "end": end.isoformat()}:
        res.errors.append(f"period ska vara {start}–{end} för {week}, är {data['period']}")

    names = _name_index(instruments)
    week_raw = raw_dir / week if raw_dir is not None else None
    # Mockveckor har påhittade källor och jämförs aldrig med riktig rådata, även
    # om data/raw/ råkar ha en mapp för samma veckonummer.
    raw_available = not is_mock and week_raw is not None and week_raw.is_dir()
    if not is_mock and not raw_available:
        res.warnings.append(f"rådata saknas ({week_raw}) – evidence-citaten kontrollerades inte mot källtexten")
    raw_index = _load_raw_index(week_raw, res) if raw_available else None

    key_quotes = _check_policy_rate(data, start, end, raw_index, is_mock, res)

    seen_sectors: set[str] = set()
    seen_items: set[str] = set()
    for s in data["sectors"]:
        sid = s["id"]
        if sid not in sector_ids:
            res.errors.append(f"sektor {sid!r} finns inte i data/sectors.json")
        if sid in seen_sectors:
            res.errors.append(f"sektor {sid!r} förekommer flera gånger")
        seen_sectors.add(sid)

        # Makrosammanfattningen får hänvisa till nyckeltalen (styrräntan).
        sector_quotes: list[str] = list(key_quotes) if sid == "makro" else []
        for item in s["items"]:
            where = f"{sid}/{item['id']}"
            if item["id"] in seen_items:
                res.errors.append(f"{where}: nyhets-id förekommer flera gånger")
            seen_items.add(item["id"])
            if item["source"]["raw_file"].startswith("riksbanken/"):
                res.errors.append(f"{where}: styrräntan är ett nyckeltal (key_figures.policy_rate), inte en nyhet")

            published = date.fromisoformat(item["published"])
            if not start <= published <= end:
                res.errors.append(f"{where}: published {published} ligger utanför {start}–{end}")

            quotes = [e["quote"] for e in item["evidence"]]
            sector_quotes += quotes
            for q in quotes:
                if len(q.split()) > MAX_QUOTE_WORDS:
                    res.errors.append(f"{where}: citatet har {len(q.split())} ord (max {MAX_QUOTE_WORDS}): {q[:60]!r}…")
            text = item["headline"] + ".\n" + item["summary"]
            missing = unsupported_numbers(text, quotes)
            if missing:
                res.errors.append(f"{where}: siffror utan exakt stöd i evidence: {missing}")
            conflicts = direction_conflicts(text, quotes)
            if conflicts:
                res.errors.append(f"{where}: riktningen motsäger evidence: {conflicts}")

            if "sentiment" in item:
                entry_for_sentiment = raw_index.get(item["source"]["raw_file"]) if raw_index is not None else None
                _check_sentiment(item, where, entry_for_sentiment, res)
            elif not is_mock:
                res.warnings.append(f"{where}: sentiment saknas")

            if raw_index is not None:
                entry = raw_index.get(item["source"]["raw_file"])
                if entry is None:
                    res.errors.append(f"{where}: raw_file {item['source']['raw_file']!r} finns inte i rådatans index")
                elif item["source"]["url"] != entry["url"]:
                    res.errors.append(
                        f"{where}: source.url {item['source']['url']!r} skiljer sig från indexets {entry['url']!r}"
                    )

            if raw_available:
                raw_path = week_raw / item["source"]["raw_file"]
                if not raw_path.is_file():
                    res.errors.append(f"{where}: källfilen {raw_path} finns inte")
                else:
                    raw_text = _normalize_ws(raw_path.read_text(encoding="utf-8"))
                    for e in item["evidence"]:
                        if _normalize_ws(e["quote"]) not in raw_text:
                            res.errors.append(f"{where}: citatet finns inte i källtexten: {e['quote']!r}")

            # Sektorer som nyheten får kopplas till indirekt: den egna plus sector_impacts.
            linked = {sid, *(imp["sector"] for imp in item["sector_impacts"])}
            for c in item["companies"]:
                _check_company(c, where, instruments, names, res)
                _check_indirect_sector(c, where, instruments, linked, res)
                _check_bid_target(c, item, where, res)

            for imp in item["sector_impacts"]:
                if imp["sector"] not in sector_ids:
                    res.errors.append(f"{where}: sector_impacts har okänd sektor {imp['sector']!r}")
                elif imp["sector"] == sid:
                    res.errors.append(f"{where}: sector_impacts pekar på nyhetens egen sektor {sid!r}")

        missing = unsupported_numbers(s["summary"], sector_quotes)
        if missing:
            res.errors.append(f"{sid}: sektorsammanfattningen har siffror utan exakt stöd i evidence: {missing}")
        conflicts = direction_conflicts(s["summary"], sector_quotes)
        if conflicts:
            res.errors.append(f"{sid}: sektorsammanfattningens riktning motsäger evidence: {conflicts}")

    if raw_available:
        _check_selection(data, week, instruments, raw_dir, res)
    return res


LARGE_CAP_PATH = DATA_DIR / "large_cap.json"


def load_large_cap(instruments: dict[str, Instrument], path: Path | None = None) -> set[str]:
    """Bolag i Nasdaq Stockholms Large Cap: de vars ISIN finns i data/large_cap.json."""
    path = path or LARGE_CAP_PATH
    if not path.exists():
        return set()
    isins = set(json.loads(path.read_text(encoding="utf-8"))["isins"])
    return {t for t, inst in instruments.items() if isins & set(inst.isin or ())}


def _check_selection(data: dict, week: str, instruments: dict[str, Instrument], raw_dir: Path, res: Result) -> None:
    """Urvalskontroll: en sektor får inte vara tom om ett Large Cap-bolag i sektorn rapporterat under veckan
    (W43 med Sonnet: telekom tom trots Tele2 och Telia). Rapporterna listas som för rapportutfallen
    (scripts/report_list.py)."""
    from report_list import list_scope  # sent: report_list importerar den här modulen

    large = load_large_cap(instruments)
    if not large:
        res.warnings.append("data/large_cap.json saknas – urvalskontrollen gjordes inte")
        return
    reported: dict[str, list[str]] = {}
    # Bara rådatan i data/raw/<vecka>/: det är den veckosammanfattningen får citera.
    for r in list_scope(week, instruments, load_sector_kpis(), raw_dir=raw_dir, morning_raw=None, results_dir=None):
        if r["ticker"] in large and r["sector"]:
            reported.setdefault(r["sector"], []).append(r["name"])
    non_empty = {s["id"] for s in data["sectors"] if s["items"]}
    for sector, names in sorted(reported.items()):
        if sector not in non_empty:
            res.errors.append(f"sektorn {sector} är tom, men Large Cap-bolag i sektorn rapporterade under veckan: "
                              f"{', '.join(sorted(names))}")


def _load_raw_index(week_raw: Path, res: Result) -> dict[str, dict] | None:
    """index.json i rådatan, nycklat på path. None (med fel) om det saknas."""
    path = week_raw / "index.json"
    if not path.is_file():
        res.errors.append(f"rådatans index saknas: {path}")
        return None
    return {e["path"]: e for e in json.loads(path.read_text(encoding="utf-8"))["items"]}


def _sv_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


# --- Sentiment ---------------------------------------------------------------

# Ord som handlar om aktien/kursen eller är råd – tonen ska bygga på nyhetens innehåll.
_SENTIMENT_FORBIDDEN_RE = re.compile(
    r"\bkurs(?:en|er|erna|uppgång\w*|nedgång\w*|rörelse\w*|mål\w*|ras\w*|lyft\w*)?\b|\baktien\b|\baktiekurs\w*"
    r"|\bköp(?:läge|rekommendation|signal|värd)\w*|\bsälj(?:läge|rekommendation|signal)\w*|\bbör (?:köpas|säljas|köpa|sälja)\b"
    r"|\bunder(?:värder|prissatt)\w*|\böver(?:värder|prissatt)\w*|\brekommend\w*|\bkursmål\w*|\binvesteringsråd\w*",
    re.IGNORECASE,
)
_CONTENT_STOPWORDS = {
    "och", "eller", "som", "för", "med", "till", "från", "efter", "under", "över", "inom", "mellan", "utan",
    "att", "det", "den", "denna", "detta", "dessa", "har", "hade", "kommer", "ska", "skulle", "kan", "blir", "blev",
    "bolaget", "bolagets", "också", "även", "samt", "vilket", "vilken", "där", "när", "nyheten", "procent",
    "with", "that", "this", "will", "from", "have", "their", "about", "which",
}


def _content_stems(text: str) -> set[str]:
    """Innehållsord (minst fyra bokstäver, inte stoppord), kortade till fem tecken
    så att böjningar möts ("förvärvar"/"förvärvet")."""
    return {w[:5] for w in (m.casefold() for m in re.findall(r"[^\W\d_]{4,}", text)) if w not in _CONTENT_STOPWORDS}


def _check_sentiment(item: dict, where: str, raw_entry: dict | None, res: Result) -> None:
    s = item["sentiment"]
    evidence = item["evidence"]
    bad = [i for i in s["basis"] if i >= len(evidence)]
    if bad:
        res.errors.append(f"{where}: sentiment.basis pekar på citat som inte finns: {bad} (evidence har {len(evidence)})")
        return
    refs = [evidence[i]["quote"] for i in s["basis"]]
    text = s["rationale"]
    missing = unsupported_numbers(text, refs)
    if missing:
        res.errors.append(f"{where}: sentiment.rationale har siffror som inte finns i de refererade citaten: {missing}")
    conflicts = direction_conflicts(text, refs)
    if conflicts:
        res.errors.append(f"{where}: sentiment.rationale motsäger riktningen i de refererade citaten: {conflicts}")
    if not _content_stems(text) & _content_stems(" ".join(refs)):
        res.errors.append(f"{where}: sentiment.rationale delar inget innehållsord med de refererade citaten")
    forbidden = sorted({m.group(0) for m in _SENTIMENT_FORBIDDEN_RE.finditer(text)})
    if forbidden:
        res.errors.append(f"{where}: sentiment.rationale får inte handla om kurs eller råd: {forbidden}")
    if raw_entry is not None and raw_entry.get("routine") and s["label"] != "neutral":
        res.errors.append(f"{where}: rutinmeddelanden ska ha sentiment 'neutral', inte {s['label']!r}")


_RATE_RE = re.compile(r"styrränt", re.IGNORECASE)
_RATE_CHANGE_RE = re.compile(r"\b(?:sänk|höj)\w*", re.IGNORECASE)
# Meningar om förväntningar eller prognoser påstår inte att räntan ändrats.
_FORECAST_RE = re.compile(r"\b(?:tror|väntar|förväntar|förväntningar|väntas|prognos\w*|bedöm\w*|signal\w*|kan komma)\b",
                          re.IGNORECASE)


def _changed_this_week(kf: dict, start: date, end: date) -> bool:
    return kf["changed_on"] is not None and start <= date.fromisoformat(kf["changed_on"]) <= end


def _rate_change_claims(text: str) -> list[str]:
    """Meningar som säger att styrräntan sänkts/höjts (inte förväntningar om det)."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [
        s.strip() for s in sentences
        if _RATE_RE.search(s) and _RATE_CHANGE_RE.search(s) and not _FORECAST_RE.search(s)
    ]


def _check_policy_rate(
    data: dict, start: date, end: date, raw_index: dict[str, dict] | None, is_mock: bool, res: Result
) -> list[str]:
    """Kontrollerar key_figures.policy_rate och returnerar "citat" som
    makrosammanfattningen får använda (värdena och datumen, i svensk form)."""
    kf = (data.get("key_figures") or {}).get("policy_rate")
    if kf is None:
        if not is_mock:
            res.warnings.append("key_figures.policy_rate saknas – styrräntan visas inte på sidan")
        return []
    if kf["source_url"] != RIKSBANK_PAGE_URL:
        res.errors.append(f"key_figures.policy_rate.source_url ska vara Riksbankens sida {RIKSBANK_PAGE_URL}")
    if not start <= date.fromisoformat(kf["date"]) <= end:
        res.errors.append(f"key_figures.policy_rate.date {kf['date']} ligger utanför {start}–{end}")
    if (kf["previous_value"] is None) != (kf["changed_on"] is None):
        res.errors.append("key_figures.policy_rate: previous_value och changed_on ska båda vara satta eller båda null")

    if raw_index is not None:
        entry = raw_index.get(kf["raw_file"])
        raw = (entry or {}).get("data", {}).get("policy_rate")
        if entry is None or entry["source"] != "riksbanken" or raw is None:
            res.errors.append(f"key_figures.policy_rate.raw_file {kf['raw_file']!r} är ingen styrräntepost i rådatans index")
        else:
            for key in ("value", "date", "previous_value", "changed_on"):
                if kf[key] != raw[key]:
                    res.errors.append(f"key_figures.policy_rate.{key} = {kf[key]!r}, rådatan säger {raw[key]!r}")
            latest = max(
                (e["data"]["policy_rate"]["date"] for e in raw_index.values()
                 if e["source"] == "riksbanken" and "policy_rate" in e.get("data", {})
                 and e["data"]["policy_rate"]["date"] <= end.isoformat()),
                default=None,
            )
            if latest is not None and kf["date"] != latest:
                res.errors.append(f"key_figures.policy_rate avser {kf['date']}, men senaste observationen i veckan är {latest}")

    if not _changed_this_week(kf, start, end):
        makro = next((s for s in data["sectors"] if s["id"] == "makro"), None)
        for sentence in _rate_change_claims(makro["summary"] if makro else ""):
            when = f"senast ändrad {kf['changed_on']}" if kf["changed_on"] else "ingen ändring i hämtad period"
            res.errors.append(
                f"makro: sammanfattningen säger att styrräntan ändrats, men den är oförändrad under veckan "
                f"({when}) – skriv \"oförändrad sedan …\": {sentence!r}"
            )

    quotes = [f"{_sv_number(kf['value'])} procent", kf["date"]]
    if kf["previous_value"] is not None:
        quotes += [f"{_sv_number(kf['previous_value'])} procent", kf["changed_on"]]
    return quotes


def _check_company(c: dict, where: str, instruments: dict[str, Instrument], names: dict[str, str], res: Result) -> None:
    label = f"{where}: bolaget {c['name']!r}"
    if c["match"] == "matchad":
        inst = instruments.get(c["ticker"])
        if inst is None:
            res.errors.append(f"{label}: ticker {c['ticker']!r} finns inte i instruments.csv")
            return
        if c["yahoo_ticker"] != inst.yahoo_ticker:
            res.errors.append(f"{label}: yahoo_ticker {c['yahoo_ticker']!r} ≠ {inst.yahoo_ticker!r} i instruments.csv")
        if c["exchange"] != inst.bors:
            res.errors.append(f"{label}: exchange {c['exchange']!r} ≠ {inst.bors!r} i instruments.csv")
    else:
        ticker = names.get(c["name"].casefold())
        if ticker is not None:
            res.errors.append(f"{label}: markerat 'ej i listan' men matchar {ticker!r} i instruments.csv")


TAKEOVER_RE = re.compile(
    r"uppköp|offentligt erbjudande|budpliktsbud|\bbud\b|avnoter|tvångsinlös"
    r"|takeover|tender offer|public offer|delist|squeeze-out|compulsory (?:acquisition|redemption)",
    re.IGNORECASE,
)


def _check_bid_target(c: dict, item: dict, where: str, res: Result) -> None:
    """bid_target får bara sitta på ett matchat, direkt berört bolag i en nyhet som
    handlar om uppköpsbud, avnotering eller tvångsinlösen."""
    if not c.get("bid_target"):
        return
    label = f"{where}: bolaget {c['name']!r} har bid_target"
    if c["match"] != "matchad" or c["impact"] != "direkt":
        res.errors.append(f"{label} men är inte ett matchat, direkt berört bolag")
    text = " ".join([item["headline"], item["summary"], *(e["quote"] for e in item["evidence"])])
    if not TAKEOVER_RE.search(text):
        res.errors.append(f"{label} men nyheten nämner inget uppköpsbud, ingen avnotering och ingen tvångsinlösen")


def bid_targets(week_data: dict) -> set[str]:
    """Tickers som är målbolag för uppköpsbud/avnotering enligt veckofilen."""
    return {
        c["ticker"] for s in week_data.get("sectors", []) for it in s["items"] for c in it["companies"]
        if c.get("bid_target") and c.get("ticker")
    }


def _check_indirect_sector(
    c: dict, where: str, instruments: dict[str, Instrument], linked: set[str], res: Result
) -> None:
    """Ett indirekt kopplat bolag måste tillhöra nyhetens sektor eller en sektor i
    dess sector_impacts – annars saknar kopplingen stöd i nyhetens egen analys.
    Bolag utan sektor i instruments.csv (och bolag som inte finns i listan) kontrolleras inte."""
    if c["impact"] != "indirekt" or c["match"] != "matchad":
        return
    inst = instruments.get(c["ticker"])
    if inst is None or inst.sektor is None:
        return
    if inst.sektor not in linked:
        res.errors.append(
            f"{where}: bolaget {c['name']!r} är indirekt kopplat men tillhör sektor {inst.sektor!r}, "
            f"som varken är nyhetens sektor eller finns i sector_impacts {sorted(linked)}"
        )


def validate_prices(data: dict, expected_week: str | None) -> Result:
    res = Result()
    res.errors += _schema_errors(_schema_validator("prices.schema.json"), data)
    if res.errors:
        return res
    if expected_week is not None and data["week"] != expected_week:
        res.errors.append(f"week {data['week']!r} matchar inte filnamnet {expected_week!r}")
    _check_datetime(data["fetched_at"], "fetched_at", res)
    index = data.get("index")
    for ticker, q in data["quotes"].items():
        rel = q.get("relative_pp")
        if rel is None:
            if index is not None and q["currency"] == "SEK" and "relative_pp" in q:
                res.errors.append(f"{ticker}: relative_pp saknas fast index finns")
            continue
        if index is None:
            res.errors.append(f"{ticker}: relative_pp utan index")
        elif q["currency"] != "SEK":
            res.errors.append(f"{ticker}: relative_pp mot Stockholmsindex för aktie i {q['currency']}")
        elif abs(q["change_pct"] - index["change_pct"] - rel) > 0.011:
            res.errors.append(f"{ticker}: relative_pp {rel} ≠ {q['change_pct']} − {index['change_pct']}")
    return res


def validate_analysis(data: dict, expected_week: str | None) -> Result:
    """Blanknings- och insynsanalysen (scripts/registers.py): schema plus att siffrorna
    hänger ihop, så att en handredigerad eller trasig fil inte visas."""
    res = Result()
    res.errors += _schema_errors(_schema_validator("analysis.schema.json"), data)
    if res.errors:
        return res
    week = data["week"]
    if expected_week is not None and week != expected_week:
        res.errors.append(f"week {week!r} matchar inte filnamnet {expected_week!r}")
    start, end = iso_week_period(week)
    if data["period"] != {"start": start.isoformat(), "end": end.isoformat()}:
        res.errors.append(f"period ska vara {start}–{end} för {week}")

    for block in ("short_positions", "short_aggregate"):
        b = data[block]
        if not b["available"] and (b["changes"] or not b["note"]):
            res.errors.append(f"{block}: ej tillgänglig ska ha en förklaring och inga förändringar")
        for c in b["changes"]:
            if abs(c["end_pct"] - c["start_pct"] - c["change_pp"]) > 0.011:
                res.errors.append(f"{block}/{c['ticker']}: change_pp {c['change_pp']} ≠ {c['end_pct']} − {c['start_pct']}")
            holders = c.get("holders", [])
            for h in holders:
                if abs(h["end_pct"] - h["start_pct"] - h["change_pp"]) > 0.011:
                    res.errors.append(f"{block}/{c['ticker']}/{h['holder']}: change_pp stämmer inte")
                # Ändringen ska ha skett efter förra veckans slut och senast vid veckans slut.
                if b["start_as_of"] and b["end_as_of"] and not b["start_as_of"] < h["date"] <= b["end_as_of"]:
                    res.errors.append(f"{block}/{c['ticker']}/{h['holder']}: positionsdatum {h['date']} utanför veckan")
            if holders and c.get("last_change_date") != max(h["date"] for h in holders):
                res.errors.append(f"{block}/{c['ticker']}: last_change_date ska vara senaste innehavardatum")

    ins = data["insider"]
    days = {(start + timedelta(days=i)).isoformat() for i in range(7)}
    if set(ins["days_covered"]) | set(ins["days_missing"]) != days or set(ins["days_covered"]) & set(ins["days_missing"]):
        res.errors.append("insider: days_covered och days_missing ska tillsammans vara veckans sju dagar")
    for c in ins["companies"]:
        where = f"insider/{c['ticker']}"
        buys = [t for t in c["transactions"] if t["side"] == "köp"]
        sells = [t for t in c["transactions"] if t["side"] == "försäljning"]
        if c["buy_value_sek"] != sum(t["value_sek"] for t in buys) or c["sell_value_sek"] != sum(t["value_sek"] for t in sells):
            res.errors.append(f"{where}: köp-/säljvärde stämmer inte med transaktionerna")
        if c["net_value_sek"] != c["buy_value_sek"] - c["sell_value_sek"]:
            res.errors.append(f"{where}: net_value_sek ≠ köp − sälj")
        if sorted(c["buyers"]) != sorted({t["person"] for t in buys}) or sorted(c["sellers"]) != sorted({t["person"] for t in sells}):
            res.errors.append(f"{where}: buyers/sellers stämmer inte med transaktionerna")
        if c["cluster_buy"] != (len(c["buyers"]) >= ins["cluster_min_persons"]) or \
                c["cluster_sell"] != (len(c["sellers"]) >= ins["cluster_min_persons"]):
            res.errors.append(f"{where}: markeringen för flera insynspersoner stämmer inte")
        for t in c["transactions"]:
            if t["value_sek"] <= ins["threshold_sek"]:
                res.errors.append(f"{where}: transaktion om {t['value_sek']} kr under tröskeln {ins['threshold_sek']}")
            if abs(t["volume"] * t["price"] - t["value_sek"]) > 1:
                res.errors.append(f"{where}: value_sek ≠ volym × pris för {t['person']}")
            if not start.isoformat() <= t["published_date"] <= end.isoformat():
                res.errors.append(f"{where}: publicerad {t['published_date']} utanför veckan")
            bd = business_days_between(date.fromisoformat(t["transaction_date"]), date.fromisoformat(t["published_date"]))
            if t["business_days_to_publication"] != bd or t["late_report"] != (bd > 3):
                res.errors.append(f"{where}: sen anmälan stämmer inte för {t['person']} ({bd} arbetsdagar)")
    return res


def _raw_urls(raw_dir: Path) -> set[str] | None:
    """Alla käll-URL:er i rådatans index, eller None om rådata saknas."""
    indexes = list(raw_dir.glob("*/index.json")) if raw_dir.exists() else []
    if not indexes:
        return None
    urls: set[str] = set()
    for p in indexes:
        for it in json.loads(p.read_text(encoding="utf-8")).get("items", []):
            urls.add(it.get("url"))
            urls.update(a.get("url") for a in it.get("also_in", []))
    return urls


def validate_calendar(data: dict, expected_week: str | None, instruments: dict[str, Instrument],
                      raw_dir: Path = RAW_DIR) -> Result:
    """Rapportkalendern (scripts/report_calendar.py): schema, att datumen ligger i veckan,
    att statusen stämmer med datumen, att bolagen finns i listan och att MFN-länkarna
    finns i rådatan (så att ingen källa är påhittad)."""
    res = Result()
    res.errors += _schema_errors(_schema_validator("calendar.schema.json"), data)
    if res.errors:
        return res
    week = data["week"]
    if expected_week is not None and week != expected_week:
        res.errors.append(f"week {week!r} stämmer inte med filnamnet {expected_week!r}")
    start, end = iso_week_period(week)
    if data["period"] != {"start": start.isoformat(), "end": end.isoformat()}:
        res.errors.append(f"period stämmer inte med {week}")
    _check_datetime(data["generated_at"], "generated_at", res)
    urls = _raw_urls(raw_dir)
    if urls is None:
        res.warnings.append("rådata saknas – MFN-länkarna kontrolleras inte")
    seen: set[tuple[str, str]] = set()
    for i, e in enumerate(data["events"]):
        where = f"events[{i}] ({e['name']}, {e['kind']})"
        if not start.isoformat() <= e["date"] <= end.isoformat():
            res.errors.append(f"{where}: {e['date']} ligger utanför veckan")
        inst = instruments.get(e["ticker"])
        if inst is None or inst.yahoo_ticker != e["yahoo_ticker"] or inst.namn != e["name"]:
            res.errors.append(f"{where}: bolaget finns inte i instruments.csv med samma namn och yahoo_ticker")
        if (e["ticker"], e["kind"]) in seen:
            res.errors.append(f"{where}: dubblett")
        seen.add((e["ticker"], e["kind"]))
        y, m = e["yahoo_date"], e["mfn_date"]
        expected = {
            "bekräftat": y is not None and y == m and e["date"] == y,
            "avvikelse": y is not None and m is not None and y != m and e["date"] in (y, m),
            "yahoo": y is not None and m is None and e["date"] == y,
            "mfn": y is None and m is not None and e["date"] == m,
        }[e["status"]]
        if not expected:
            res.errors.append(f"{where}: status {e['status']!r} stämmer inte med datumen (Yahoo {y}, MFN {m})")
        if e["kind"] == "stämma" and e["status"] != "mfn":
            res.errors.append(f"{where}: stämmor kommer bara från MFN")
        if (m is None) != (e["mfn_url"] is None) or (m is None) != (e["mfn_title"] is None):
            res.errors.append(f"{where}: mfn_date, mfn_url och mfn_title ska finnas tillsammans")
        if urls is not None and e["mfn_url"] is not None and e["mfn_url"] not in urls:
            res.errors.append(f"{where}: {e['mfn_url']} finns inte i rådatan")
    return res


def validate_swing(data: dict, instruments: dict[str, Instrument]) -> Result:
    """Swing (scripts/swing.py): schema, bolagen finns i listan och etiketterna stämmer med måtten och
    trösklarna i filen (räknas om här). Inga ord som uppmanar till köp eller försäljning i etiketterna."""
    from swing import report_label, technical_labels  # sent: swing.py importerar den här modulen

    res = Result()
    res.errors += _schema_errors(_schema_validator("swing.schema.json"), data)
    if res.errors:
        return res
    _check_datetime(data["generated_at"], "generated_at", res)
    names = [*data["labels"].items(), *((f"strategi {k}", s["name"]) for k, s in data.get("strategies", {}).items())]
    for k, text in names:
        if re.search(r"\b(köp|sälj|kursmål|rekommend)", text, re.IGNORECASE):
            res.errors.append(f"etiketten {k!r} innehåller en uppmaning: {text!r}")
    seen = set()
    for c in data["companies"]:
        where = f"{c['name']} ({c['ticker']})"
        inst = instruments.get(c["ticker"])
        if inst is None or inst.namn != c["name"]:
            res.errors.append(f"{where}: finns inte i instruments.csv med samma namn")
            continue
        if c["ticker"] in seen:
            res.errors.append(f"{where}: förekommer flera gånger")
        seen.add(c["ticker"])
        expected = technical_labels(c, data["thresholds"])
        rl = report_label(c["days_since_report"], c["report_verdict"], data["thresholds"])
        if rl:
            expected.append(rl)
        if "pullback" in c:
            _check_pullback(c, data["thresholds"].get("pullback"), where, expected, res)
        if c.get("sr"):
            _check_sr(c["sr"], where, res)
        if sorted(c["labels"]) != sorted(expected):
            res.errors.append(f"{where}: etiketterna {c['labels']} stämmer inte med måtten (väntat {expected})")
        unknown = set(c["labels"]) - set(data["labels"])
        if unknown:
            res.errors.append(f"{where}: okända etiketter {sorted(unknown)}")
    return res


def _check_sr(sr: dict, where: str, res: Result) -> None:
    """Stöd och motstånd (scripts/levels.py): stödets övre kant (som visas) högst motståndets nedre kant (ett
    medelvärde inne i en zon räknas inte som egen nivå), stoppet under stödet, och motståndet i R =
    (motståndets nedre kant − triggernivå) / (triggernivå − stopp)."""
    sup, rst = sr["support"], sr["resistance"]
    if sup and rst and sup["high"] > rst["low"] + 1e-9:
        res.errors.append(f"{where}: stödet {sup['high']} ({sup['kind']}) ligger över motståndet {rst['low']} ({rst['kind']})")
    if sup and (sr["stop"] is None or sr["stop"] >= sup["low"]):
        res.errors.append(f"{where}: stoppet {sr['stop']} ligger inte under stödet {sup['low']}")
    for side, lv in (("stödet", sup), ("motståndet", rst)):
        if lv and lv.get("inside") and (lv["kind"] != "zon" or lv["dist_pct"] not in (0, 0.0, None)):
            res.errors.append(f"{where}: {side} är markerat \"inne i zonen\" men är ingen zon eller har avstånd")
    if rst and sr["stop"] is not None and sr["trigger"] > sr["stop"]:
        from levels import resistance_edge

        # Inne i motståndszonen räknas R till zonens övre kant, annars till den nedre.
        expected = round((resistance_edge(rst) - sr["trigger"]) / (sr["trigger"] - sr["stop"]), 2)
        if sr["resistance_r"] is None or abs(sr["resistance_r"] - expected) > 0.011:
            res.errors.append(f"{where}: motståndet i R {sr['resistance_r']} ≠ {expected}")


def _check_pullback(c: dict, th: dict | None, where: str, expected: list[str], res: Result) -> None:
    """Pullback i trend: etiketten räknas om ur det sparade tillståndet (scripts/pullback.py), och nivåerna
    ska finnas vid träff och hänga ihop (stopp < referens, 2R/3R = referens + 2/3 × R, trigger = high)."""
    import pullback

    pb = c["pullback"]
    labels = pullback.labels(pb["state"], th or pullback.THRESHOLDS)
    expected += labels
    lv = pb["levels"]
    if bool(labels) != (lv is not None):
        res.errors.append(f"{where}: pullback-nivåerna ska finnas om och endast om etiketten finns")
        return
    if lv is None:
        if pb.get("bollinger") is not None:
            res.errors.append(f"{where}: Bollingerläget finns utan pullback-nivåer")
        return
    if pb.get("bollinger") is not None:
        from bollinger import position_errors

        res.errors += position_errors(pb["bollinger"], f"{where} Bollinger")
        if abs(pb["bollinger"]["price"] - pb["state"]["close"]) > 1e-3:
            res.errors.append(f"{where}: Bollingerlägets kurs är inte gårdagens stängning")
    risk = lv["ref"] - lv["stop"]
    if risk <= 0:
        res.errors.append(f"{where}: stoppet {lv['stop']} ligger inte under referensnivån {lv['ref']}")
    for key, k in (("r2", 2), ("r3", 3)):
        if abs(lv[key] - (lv["ref"] + k * risk)) > 1e-3:
            res.errors.append(f"{where}: {key} {lv[key]} ≠ referens + {k}R")
    sig = pullback.signal(pb["state"], th or pullback.THRESHOLDS)
    if lv["signal"] != sig or (sig and lv["trigger"] != pb["state"]["high"]) or (not sig and lv["trigger"] is not None):
        res.errors.append(f"{where}: vändsignal eller triggernivå stämmer inte med gårdagens candle")
    if lv["stop"] > lv["level_value"] + 1e-9:
        res.errors.append(f"{where}: stoppet ligger över MA-nivån")


def validate_signals(data: dict, expected_week: str | None, instruments: dict[str, Instrument]) -> Result:
    """Signalöversikten (scripts/signals.py): schema, bolagen finns i listan och
    signalerna stämmer med värdena och trösklarna i filen (räknas om här)."""
    from signals import signals_for  # sent, eftersom signals.py importerar den här modulen

    res = Result()
    res.errors += _schema_errors(_schema_validator("signals.schema.json"), data)
    if res.errors:
        return res
    week = data["week"]
    if expected_week is not None and week != expected_week:
        res.errors.append(f"week {week!r} stämmer inte med filnamnet {expected_week!r}")
    start, end = iso_week_period(week)
    if data["period"] != {"start": start.isoformat(), "end": end.isoformat()}:
        res.errors.append(f"period stämmer inte med {week}")
    _check_datetime(data["generated_at"], "generated_at", res)
    seen: set[str] = set()
    for c in data["companies"]:
        where = f"{c['name']} ({c['ticker']})"
        inst = instruments.get(c["ticker"])
        if inst is None or inst.yahoo_ticker != c["yahoo_ticker"] or inst.namn != c["name"]:
            res.errors.append(f"{where}: bolaget finns inte i instruments.csv med samma namn och yahoo_ticker")
        if c["ticker"] in seen:
            res.errors.append(f"{where}: dubblett")
        seen.add(c["ticker"])
        if (c["tone"] is None) != (c["news"] == 0):
            res.errors.append(f"{where}: ton utan nyheter eller nyheter utan ton")
        if c["relative_pp"] is not None and (c["change_pct"] is None or data["index"] is None):
            res.errors.append(f"{where}: jämförelse mot index utan kurs eller index")
        elif c["relative_pp"] is not None and abs(c["change_pct"] - data["index"]["change_pct"] - c["relative_pp"]) > 0.011:
            res.errors.append(f"{where}: relative_pp ≠ förändring − indexets förändring")
        if (c["from_high_pct"] or 0) > 0 or (c["from_low_pct"] or 0) < 0:
            res.errors.append(f"{where}: stängningen ligger utanför 52-veckorsintervallet")
        expected = signals_for(c, data["thresholds"])
        if sorted(c["signals"]) != sorted(expected):
            res.errors.append(f"{where}: signalerna {c['signals']} stämmer inte med värdena (väntat {expected})")
    return res


def validate_valuation(data: dict, expected_week: str | None, instruments: dict[str, Instrument],
                       actions_path: Path = DATA_DIR / "corporate_actions.json") -> Result:
    """Kurs mot vinst (scripts/valuation.py): schema, bolagen finns i listan, och kurs i år, P/E,
    förändringar, tolkning och status räknas om ur värdena i filen. Justerade ex-dagar måste
    finnas som verifierade poster i data/corporate_actions.json."""
    res = Result()
    res.errors += _schema_errors(_schema_validator("valuation.schema.json"), data)
    if res.errors:
        return res
    if expected_week is not None and data["week"] != expected_week:
        res.errors.append(f"week {data['week']!r} stämmer inte med filnamnet {expected_week!r}")
    _check_datetime(data["generated_at"], "generated_at", res)
    th = data["thresholds"]
    verified: dict[str, set[str]] = {}
    known: dict[str, dict[str, str]] = {}  # ticker -> {yahoo-datum: etikett}
    missing_known: dict[str, str] = {}  # ticker -> etikett när kursen vid årets början saknas
    if actions_path.exists():
        ca = json.loads(actions_path.read_text(encoding="utf-8"))
        for a in ca.get("actions", []):
            verified.setdefault(a["ticker"], set()).add(a["ex_date"])
        for e in ca.get("investigated", []):
            if e.get("label"):
                known.setdefault(e["ticker"], {}).update({d: e["label"] for d in e.get("yahoo_dates", [])})
                if e.get("missing_price"):
                    missing_known[e["ticker"]] = e["label"]
        listings = {x["ticker"]: x["first_trading_day"] for x in ca.get("listings", [])}
    else:
        listings = {}
    idx = data["index"]["ytd_pct"]
    close = lambda a, b, tol=0.051: a is not None and b is not None and abs(a - b) <= tol  # noqa: E731
    for c in data["companies"]:
        w = f"{c['name']} ({c['ticker']})"
        inst = instruments.get(c["ticker"])
        if inst is None or inst.namn != c["name"]:
            res.errors.append(f"{w}: finns inte i instruments.csv med samma namn")
            continue
        if not set(c["adjusted_ex_dates"]) <= verified.get(c["ticker"], set()):
            res.errors.append(f"{w}: justering utan verifierad bolagshändelse i corporate_actions.json")
        unexplained = [m for m in c["big_moves"] if m["date"] not in c["adjusted_ex_dates"]]
        if any(abs(m["pct"]) <= th["big_move_pct"] for m in c["big_moves"]):
            res.errors.append(f"{w}: big_moves innehåller en rörelse under gränsen")
        st = c["status"]
        has_prices = c["price_start"] is not None and c["price_now"] is not None
        all_known = bool(unexplained) and all(m["date"] in known.get(c["ticker"], {}) for m in unexplained)
        listed_on = listings.get(c["ticker"])
        listed = (c["price_start"] is None and listed_on is not None
                  and data["year_start"] <= listed_on <= data["as_of"])
        if (st == "noterad under året") != listed:
            res.errors.append(f"{w}: status {st!r} stämmer inte med verifierad notering under året")
        # Saknad kurs vid årets början som förklaras av en känd händelse (missing_price).
        missing_explained = (c["price_start"] is None and not listed and c["ticker"] in missing_known)
        if missing_explained:
            expected_st = "känd bolagshändelse"
        else:
            expected_st = (None if not (unexplained and has_prices) else
                           "känd bolagshändelse" if all_known else "kontrollera bolagshändelse")
        if (st in ("kontrollera bolagshändelse", "känd bolagshändelse") or expected_st) and st != expected_st:
            res.errors.append(f"{w}: status {st!r} stämmer inte med kursrörelserna eller saknad kurs "
                              f"(väntat {expected_st!r})")
        if listed:
            from valuation import listing_label  # sent, eftersom valuation.py importerar den här modulen

            expected_label = listing_label(listed_on)
        elif missing_explained:
            expected_label = missing_known[c["ticker"]]
        else:
            expected_label = ("; ".join(dict.fromkeys(known[c["ticker"]][m["date"]] for m in unexplained))
                              if expected_st == "känd bolagshändelse" else None)
        if c["event_label"] != expected_label:
            res.errors.append(f"{w}: etiketten {c['event_label']!r} ≠ {expected_label!r}")
        if st in ("kontrollera bolagshändelse", "känd bolagshändelse", "noterad under året", "kurs saknas"):
            if any(c[k] is not None for k in ("ytd_pct", "pe_start", "pe_now", "pe_change_pct", "verdict")):
                res.errors.append(f"{w}: kurs i år eller P/E visas trots status {st!r}")
            continue
        ytd = (c["price_now"] / c["price_start"] - 1) * 100
        if not close(c["ytd_pct"], ytd, 0.006):
            res.errors.append(f"{w}: kurs i år {c['ytd_pct']} ≠ {ytd:.2f}")
        if idx is not None and not close(c["rel_index_pp"], c["ytd_pct"] - idx, 0.011):
            res.errors.append(f"{w}: mot index stämmer inte")
        if st == "fastighetsbolag" and inst.sektor != "fastigheter" or st == "investmentbolag" and inst.typ != "investmentbolag":
            res.errors.append(f"{w}: status {st!r} stämmer inte med sektor/typ")
        e0, e1 = c["eps_ttm_start"], c["eps_ttm_now"]
        if st == "ok":
            if not (e0 and e1 and e0 > 0 and e1 > 0) or c["pe_start"] is None or c["pe_now"] is None:
                res.errors.append(f"{w}: status ok utan positiv vinst och P/E")
                continue
            if not close(c["pe_start"], c["price_start"] / e0) or not close(c["pe_now"], c["price_now"] / e1):
                res.errors.append(f"{w}: P/E stämmer inte med kurs och vinst")
            over = c["pe_start"] > th["pe_max"] or c["pe_now"] > th["pe_max"]
            if c["pe_over_max"] != over:
                res.errors.append(f"{w}: pe_over_max stämmer inte")
            if not over:
                if not close(c["pe_change_pct"], (c["pe_now"] / c["pe_start"] - 1) * 100, 0.051):
                    res.errors.append(f"{w}: P/E-förändringen stämmer inte")
                pc = c["pe_change_pct"]
                expected = ("kursen före vinsten" if pc > th["verdict_pct"] else
                            "vinsten före kursen" if pc < -th["verdict_pct"] else "i takt")
                if c["verdict"] != expected:
                    res.errors.append(f"{w}: tolkningen {c['verdict']!r} ≠ {expected!r}")
        elif any(c[k] is not None for k in ("pe_start", "pe_now", "pe_change_pct", "verdict")):
            res.errors.append(f"{w}: P/E visas trots status {st!r}")
        if c["eps_change_pct"] is not None and not (e0 and e1 and e0 > 0 and e1 > 0
                                                    and abs(e1 / e0 - 1) * 100 <= th["eps_change_max_pct"]
                                                    and close(c["eps_change_pct"], (e1 / e0 - 1) * 100, 0.06)):
            res.errors.append(f"{w}: vinstförändringen stämmer inte")
    return res


def validate_consensus(data: dict, instruments: dict[str, Instrument]) -> Result:
    """Konsensusfilen (scripts/consensus.py): schema, bolagen finns i listan och snittet
    ligger mellan lägsta och högsta prognos."""
    res = Result()
    res.errors += _schema_errors(_schema_validator("consensus.schema.json"), data)
    if res.errors:
        return res
    _check_datetime(data["fetched_at"], "fetched_at", res)
    seen: set[str] = set()
    for c in data["companies"]:
        where = f"{c['name']} ({c['ticker']})"
        inst = instruments.get(c["ticker"])
        if inst is None or inst.namn != c["name"] or inst.yahoo_ticker != c["yahoo_ticker"]:
            res.errors.append(f"{where}: finns inte i instruments.csv med samma namn och yahoo_ticker")
        if c["ticker"] in seen:
            res.errors.append(f"{where}: dubblett")
        seen.add(c["ticker"])
        for key in ("eps", "revenue"):
            e = c[key]
            if e and e["low"] is not None and e["high"] is not None and not e["low"] - 1e-9 <= e["avg"] <= e["high"] + 1e-9:
                res.errors.append(f"{where}: {key} snitt {e['avg']} ligger utanför {e['low']}–{e['high']}")
    return res


def validate_afternoon(data: dict, day: str, instruments: dict[str, Instrument]) -> Result:
    """Eftermiddagsuppdateringen (scripts/afternoon.py): schema, handelsdag, dagens körningar,
    bolagen finns i listan, rapporterna ligger från förra handelsdagens stängning och framåt, och
    kursreaktionen stämmer (ingen efter stängning, mot index = dag − index)."""
    from morning import is_business_day, previous_business_day  # sent, morning.py importerar den här modulen

    res = Result()
    res.errors += _schema_errors(_schema_validator("afternoon.schema.json"), data)
    if res.errors:
        return res
    if data["date"] != day:
        res.errors.append(f"date {data['date']} stämmer inte med filnamnet {day}")
    d = date.fromisoformat(data["date"])
    if not is_business_day(d):
        res.errors.append(f"{d} är ingen handelsdag")
    _check_datetime(data["generated_at"], "generated_at", res)
    if res.errors:
        return res
    for s in data["scopes"]:
        if s[:10] != data["date"]:
            res.errors.append(f"scopes: {s} är inte en körning {data['date']}")
    since = datetime.combine(previous_business_day(d), time(17, 30), ZoneInfo("Europe/Stockholm"))
    close = datetime.combine(d, time(17, 30), ZoneInfo("Europe/Stockholm"))
    index_change = data["index"]["change_pct"]
    seen = set()
    for c in data["companies"]:
        where = f"{c['name']} ({c['ticker']})"
        inst = instruments.get(c["ticker"])
        if inst is None or inst.namn != c["name"]:
            res.errors.append(f"{where} finns inte i instruments.csv")
            continue
        if c["ticker"] in seen:
            res.errors.append(f"{where} förekommer flera gånger")
        seen.add(c["ticker"])
        if c["scope"] not in data["scopes"]:
            res.errors.append(f"{where}: körningen {c['scope']} finns inte i scopes")
        _check_datetime(c["published"], f"{where} published", res)
        published = datetime.fromisoformat(c["published"])
        if published < since:
            res.errors.append(f"{where}: rapporten {c['published']} är äldre än förra handelsdagens stängning")
        if c["after_close"] != (published >= close):
            res.errors.append(f"{where}: after_close stämmer inte med publiceringstiden")
        if c["after_close"] and c["change_pct"] is not None:
            res.errors.append(f"{where}: kursreaktion trots rapport efter stängning")
        if (c["change_pct"] is None) != (c["close"] is None):
            res.errors.append(f"{where}: kurs och förändring ska finnas båda eller ingen")
        if "links" in c and c["links"] != inst.analyst_links():
            res.errors.append(f"{where}: analytikerlänkarna stämmer inte med instruments.csv")
        if c["relative_pp"] is not None:
            if c["change_pct"] is None or index_change is None or not inst.yahoo_ticker.endswith(".ST"):
                res.errors.append(f"{where}: mot index utan kurs, index eller kronor")
            elif abs(c["relative_pp"] - round(c["change_pct"] - index_change, 2)) > 0.011:
                res.errors.append(f"{where}: mot index {c['relative_pp']} ≠ {c['change_pct']} − {index_change}")
    if data["price_day"] not in (None, data["date"]):
        res.errors.append(f"price_day {data['price_day']} är inte {data['date']}")
    if data["price_day"] is None and any(c["change_pct"] is not None for c in data["companies"]):
        res.errors.append("kursreaktion utan kurser i dag (price_day saknas)")
    return res


def validate_morning(data: dict, instruments: dict[str, Instrument]) -> Result:
    """Morgonrapporten (scripts/morning.py): schema, handelsdag, bolagen finns i listan,
    nyheterna ligger i tidsfönstret och kurssignalerna stämmer med värdena."""
    from morning import is_business_day, price_signals  # sent, eftersom morning.py importerar den här modulen

    res = Result()
    res.errors += _schema_errors(_schema_validator("morning.schema.json"), data)
    if res.errors:
        return res
    day = date.fromisoformat(data["date"])
    if not is_business_day(day):
        res.errors.append(f"{day} är ingen handelsdag")
    _check_datetime(data["generated_at"], "generated_at", res)
    _check_datetime(data["news_since"], "news_since", res)
    if res.errors:
        return res
    since, until = datetime.fromisoformat(data["news_since"]), datetime.fromisoformat(data["generated_at"])

    def known(ticker: str, name: str, where: str) -> None:
        inst = instruments.get(ticker)
        if inst is None or inst.namn != name:
            res.errors.append(f"{where}: {name} ({ticker}) finns inte i instruments.csv")

    for e in data["reports_today"]:
        known(e["ticker"], e["name"], "reports_today")
        if data["date"] not in (e["yahoo_date"], e["mfn_date"]):
            res.errors.append(f"reports_today: {e['name']} har inget datum i dag")
    reporting = {e["ticker"] for e in data["reports_today"] if e["kind"] == "rapport"}
    for c in data["news"]:
        known(c["ticker"], c["name"], "news")
        if c["report_today"] != (c["ticker"] in reporting):
            res.errors.append(f"news: {c['name']}: report_today stämmer inte med dagens rapporter")
        for it in c["items"]:
            if not since <= datetime.fromisoformat(it["published"]) <= until:
                res.errors.append(f"news: {c['name']}: {it['published']} ligger utanför {since}–{until}")
    for c in data["insider"]["companies"]:
        known(c["ticker"], c["name"], "insider")
        if c["net_value_sek"] != c["buy_value_sek"] - c["sell_value_sek"]:
            res.errors.append(f"insider: {c['name']}: netto ≠ köp − sälj")
    for key in ("positions", "aggregate"):
        for c in data["short"][key]:
            known(c["ticker"], c["name"], f"short.{key}")
            if abs(c["end_pct"] - c["start_pct"] - c["change_pp"]) > 0.011:
                res.errors.append(f"short.{key}: {c['name']}: förändring ≠ slut − start")
    pd = data.get("policy_decision")
    if pd and (pd["effective_from"] < pd["decided_on"] or pd["decided_on"] > data["date"]):
        res.errors.append(f"policy_decision: beslutet {pd['decided_on']} / tillämpas {pd['effective_from']} stämmer inte")
    pr = data["price"]
    if pr.get("expected_day"):
        # Kursdatumet kommer ur datan: aldrig senare än förra handelsdagen, och avviker det ska rapporten
        # vara markerad för nytt försök (retry "kurser") med ett fel som visas som varning.
        if pr["day"] is not None and pr["day"] > pr["expected_day"]:
            res.errors.append(f"price: dagsdatans datum {pr['day']} ligger efter förra handelsdagen {pr['expected_day']}")
        if pr["day"] != pr["expected_day"] and "kurser" not in (data.get("retry") or []):
            res.errors.append(f"price: dagsdatan gäller {pr['day']}, inte {pr['expected_day']}, men retry saknar \"kurser\"")
        cov = pr.get("coverage")
        if cov and not cov["with_day"] <= cov["of"] >= cov["with_expected"]:
            res.errors.append("price: täckningen stämmer inte (fler bolag med kursrad än bolag med kurser)")
    for c in data["price"]["companies"]:
        known(c["ticker"], c["name"], "price")
        expected = price_signals(c, data["price"]["thresholds"])
        if sorted(c["signals"]) != sorted(expected):
            res.errors.append(f"price: {c['name']}: signalerna {c['signals']} stämmer inte med värdena (väntat {expected})")
    return res


def _default_targets() -> list[Path]:
    return sorted(
        [*DATA_DIR.glob("weeks/*.json"), *DATA_DIR.glob("prices/*.json"), *DATA_DIR.glob("analysis/*.json"),
         *DATA_DIR.glob("calendar/*.json"), *DATA_DIR.glob("signals/*.json"), *DATA_DIR.glob("morning/*.json"),
         *DATA_DIR.glob("consensus/*.json"), *DATA_DIR.glob("results/*.json"),
         *DATA_DIR.glob("valuation/*.json"),
         *DATA_DIR.glob("results/*/omgang-*.json"), *DATA_DIR.glob("results/morning/*.json"),
         *DATA_DIR.glob("results/morning/*/omgang-*.json"), *DATA_DIR.glob("results/updates/*.json"),
         *DATA_DIR.glob("results/updates/*/omgang-*.json"), *DATA_DIR.glob("afternoon/*.json"),
         *(p for p in DATA_DIR.glob("swing/*.json") if p.stem != "history"),
         *DATA_DIR.glob("mock/weeks/*.json"), *DATA_DIR.glob("mock/prices/*.json"),
         *DATA_DIR.glob("mock/analysis/*.json"), *DATA_DIR.glob("mock/calendar/*.json"),
         *DATA_DIR.glob("mock/signals/*.json"), *DATA_DIR.glob("mock/morning/*.json")]
    )


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv] or _default_targets()
    instruments = load_instruments()
    mock_instruments = load_instruments(MOCK_DIR / "instruments.csv")
    sector_ids = load_sector_ids()
    failed = False
    for path in targets:
        data = json.loads(path.read_text(encoding="utf-8"))
        in_mock_dir = MOCK_DIR in path.resolve().parents
        # Rapportutfall: data/results/<vecka>/omgang-N.json, data/results/morning|updates/<läge>/omgang-N.json,
        # data/results/<vecka>.json och data/results/morning|updates/<läge>.json.
        runs = ("morning", "updates")
        in_results = path.parent.name == "results" or (path.parent.name in runs
                                                       and path.parent.parent.name == "results")
        batch_in_results = path.parent.parent.name == "results" or (path.parent.parent.name in runs
                                                                    and path.parent.parent.parent.name == "results")
        if batch_in_results and re.fullmatch(r"omgang-\d+", path.stem):
            from validate_results import validate_batch

            res = validate_batch(data, path.parent.name, int(path.stem.split("-")[1]), instruments)
        elif in_results:
            from validate_results import validate_results

            res = validate_results(data, path.stem, instruments)
        elif path.parent.name == "swing" and path.stem != "history":
            res = validate_swing(data, mock_instruments if in_mock_dir else instruments)
        elif path.parent.name == "swing":
            res = Result()  # historiken (scripts/swing_history.py) har inget schema att kontrollera
        elif path.parent.name == "afternoon":
            res = validate_afternoon(data, path.stem, mock_instruments if in_mock_dir else instruments)
        elif path.parent.name == "prices":
            res = validate_prices(data, path.stem)
        elif path.parent.name == "analysis":
            res = validate_analysis(data, path.stem)
        elif path.parent.name == "valuation":
            res = validate_valuation(data, path.stem, mock_instruments if in_mock_dir else instruments)
        elif path.parent.name == "consensus":
            res = validate_consensus(data, mock_instruments if in_mock_dir else instruments)
        elif path.parent.name == "morning":
            res = validate_morning(data, mock_instruments if in_mock_dir else instruments)
        elif path.parent.name == "signals":
            res = validate_signals(data, path.stem, mock_instruments if in_mock_dir else instruments)
        elif path.parent.name == "calendar":
            # Mockkalendern länkar till påhittade MFN-poster, så rådatan kontrolleras inte där.
            res = validate_calendar(data, path.stem, mock_instruments if in_mock_dir else instruments,
                                    MOCK_DIR / "raw" if in_mock_dir else RAW_DIR)
        else:
            res = validate_week(data, path.stem, mock_instruments if in_mock_dir else instruments, sector_ids)
        if data.get("mock", False) != in_mock_dir:
            res.errors.append(
                'filer under data/mock/ ska ha "mock": true, och övriga filer får inte vara mock'
            )
        status = "FEL" if res.errors else "OK"
        print(f"[{status}] {path}")
        for w in res.warnings:
            print(f"    varning: {w}")
        for e in res.errors:
            print(f"    fel: {e}")
        failed |= bool(res.errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
