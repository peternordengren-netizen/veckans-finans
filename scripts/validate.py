"""Validerar veckofiler och kursfiler mot schema och mot projektets referensdata.

Utöver JSON Schema-kontrollen görs korskontroller som schemat inte kan uttrycka:
- veckonummer stämmer med filnamn och period (ISO-vecka, mån–sön)
- sektorer finns i data/sectors.json
- varje "matchad" ticker finns i data/instruments.csv med samma yahoo_ticker/börs
  (skydd mot påhittade tickers)
- inget bolag markerat "onoterat" finns i instruments.csv (missad matchning)
- ett indirekt kopplat bolag tillhör (enligt kolumnen sektor) nyhetens sektor
  eller en sektor i nyhetens sector_impacts
- publiceringsdatum ligger inom veckan
- varje siffra i rubrik/sammanfattning finns exakt (efter normalisering, se
  extract_numbers) i något evidence-citat – avrundade siffror underkänns
- riktningsord före ett tal (ökade/sjönk m.fl.) motsäger inte citatets tecken
  eller riktningsord (direction_conflicts)
- evidence-citat är högst 25 ord
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
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

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
    r"(?:[.,](?P<frac>\d+))?"
    r"(?P<pct> ?(?:%|procent(?!enhet)))?"
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


def extract_numbers(text: str) -> list[Number]:
    """Plockar ut alla tal ur texten efter normalisering av mellanslag, decimaltecken och minus."""
    text = text.translate(_SPACE_CHARS)
    out = []
    prev_end = 0
    for m in _NUMBER_RE.finditer(text):
        digits = m["int"].replace(" ", "") + ("." + m["frac"] if m["frac"] else "")
        value = Decimal(digits)
        if m["sign"]:
            value = -value
        before = text[prev_end:m.start()]
        words = _WORD_RE.findall(before)
        level = bool(words) and words[-1].casefold() == "till" and before.rstrip().casefold().endswith("till")
        out.append(Number(
            value=value,
            percent=bool(m["pct"]),
            raw=m.group(0).strip(),
            direction=_direction_before(before),
            level=level,
        ))
        prev_end = m.end()
    return out


def unsupported_numbers(text: str, quotes: list[str]) -> list[str]:
    """Returnerar talen i text som inte beläggs exakt av något tal i quotes."""
    supporting = [n for q in quotes for n in extract_numbers(q)]
    return [n.raw for n in extract_numbers(text) if not any(n.supported_by(s) for s in supporting)]


def direction_conflicts(text: str, quotes: list[str]) -> list[str]:
    """Returnerar talen i text vars riktningsord motsäger citatet.

    Ett ökningsord före talet kräver att något belägg i citaten går uppåt, och ett
    minskningsord kräver att något belägg går nedåt (se Number.sign). Tal utan
    riktningsord, nivåer ("sänktes till 1,75") och tal som saknar belägg helt
    (fångas av unsupported_numbers) kontrolleras inte här. "med" kontrolleras.
    """
    supporting = [n for q in quotes for n in extract_numbers(q)]
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


MFN_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Instrument:
    namn: str
    alias: tuple[str, ...]
    ticker: str
    yahoo_ticker: str
    bors: str
    mfn_slug: str | None = None  # bolagets slug i MFN:s URL:er (mfn.se/a/<slug>/…), valfri
    sektor: str | None = None  # id ur data/sectors.json, valfri


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
    with path.open(encoding="utf-8-sig", newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            alias = tuple(a.strip() for a in (row.get("alias") or "").split("|") if a.strip())
            slug = (row.get("mfn_slug") or "").strip() or None
            sektor = (row.get("sektor") or "").strip() or None
            inst = Instrument(
                namn=row["namn"].strip(),
                alias=alias,
                ticker=row["ticker"].strip(),
                yahoo_ticker=row["yahoo_ticker"].strip(),
                bors=row["börs"].strip(),
                mfn_slug=slug,
                sektor=sektor,
            )
            if inst.ticker in instruments:
                raise ValueError(f"{path.name} rad {line}: dubblett av ticker {inst.ticker!r}")
            if sektor is not None and sektor not in sector_ids:
                raise ValueError(f"{path.name} rad {line}: okänd sektor {sektor!r} (se data/sectors.json)")
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

    return res


def _load_raw_index(week_raw: Path, res: Result) -> dict[str, dict] | None:
    """index.json i rådatan, nycklat på path. None (med fel) om det saknas."""
    path = week_raw / "index.json"
    if not path.is_file():
        res.errors.append(f"rådatans index saknas: {path}")
        return None
    return {e["path"]: e for e in json.loads(path.read_text(encoding="utf-8"))["items"]}


def _sv_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


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
            res.errors.append(f"{label}: markerat onoterat men matchar {ticker!r} i instruments.csv")


def _check_indirect_sector(
    c: dict, where: str, instruments: dict[str, Instrument], linked: set[str], res: Result
) -> None:
    """Ett indirekt kopplat bolag måste tillhöra nyhetens sektor eller en sektor i
    dess sector_impacts – annars saknar kopplingen stöd i nyhetens egen analys.
    Bolag utan sektor i instruments.csv (och onoterade) kontrolleras inte."""
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
    return res


def _default_targets() -> list[Path]:
    return sorted(
        [*DATA_DIR.glob("weeks/*.json"), *DATA_DIR.glob("prices/*.json"),
         *DATA_DIR.glob("mock/weeks/*.json"), *DATA_DIR.glob("mock/prices/*.json")]
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
        if path.parent.name == "prices":
            res = validate_prices(data, path.stem)
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
