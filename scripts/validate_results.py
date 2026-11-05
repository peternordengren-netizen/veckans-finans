"""Validering av rapportutfall: omgångsfiler från /rapportutfall och den sammanslagna filen
från scripts/results.py.

Samma princip som för nyheterna – Claude skriver, valideringen kontrollerar mot källtexten:
- bara bolagen och rapportfilerna som scripts/report_list.py listar för omgången, alla med
- bara bolagets nyckeltal (sektorns plus omsättning och resultat per aktie)
- citatet: högst 25 ord och ordagrant i rapporttexten (efter normalisering av mellanslag)
- årets värde, föregående års värde och en eventuell procentsats finns som tal i citatet
- förändringen i procent stämmer med värdena (±1,5 procentenheter för avrundning)
- enheten passar nyckeltalets typ (belopp, procent, per aktie …)
- perioden: närmaste periodangivelse före citatet i rapporten ("juli–september",
  "tredje kvartalet", "januari–september" …) ska vara samma sorts period som uppgetts
  (kvartal, delår eller helår), så att kvartalets siffror inte blandas med årets hittills

En omgång valideras per rapport (check_batch): en underkänd rapport underkänner inte de andra.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from validate import (
    _SENTIMENT_FORBIDDEN_RE, DATA_DIR, _content_stems, RAW_DIR, Instrument, Result, _check_datetime, _normalize_ws, _schema_errors,
    _schema_validator, direction_conflicts, extract_numbers, iso_week_period, load_sector_kpis, unsupported_numbers,
)

MAX_QUOTE_WORDS = 25
CHANGE_TOLERANCE_PP = 1.5
PERIOD_LOOKBACK_CHARS = 2500

CURRENCIES = {"SEK", "EUR", "USD", "DKK", "NOK"}
SCALES = {"T": Decimal(1_000), "M": Decimal(1_000_000), "MD": Decimal(1_000_000_000)}
UNIT_KINDS = {
    "belopp": {f"{s}{c}" for s in SCALES for c in CURRENCIES} | CURRENCIES,
    "per_aktie": CURRENCIES,
    "procent": {"procent"},
    "volym": {"ton", "kton", "Mton", "st", "MWh", "GWh", "TWh", "boe", "kboe", "oz", "koz"},
    "pris": {f"{c}/{u}" for c in CURRENCIES | {f"T{c}" for c in CURRENCIES} for u in ("ton", "oz", "MWh", "boe", "kg")},
}


# Per aktie i hundradelar, som rapporten skriver det: "Euro cent", "EUR cent", "US cent", "öre" …
# (Hexagon 2026-10-23). Jämförs efter att mellanslag tagits bort och små bokstäver.
CENT_UNITS = {"eurocent": "EUR", "eurocents": "EUR", "eurcent": "EUR", "eurcents": "EUR", "usdcent": "USD",
              "usdcents": "USD", "uscent": "USD", "uscents": "USD", "öre": "SEK", "nokøre": "NOK", "dkkøre": "DKK"}


def cent_currency(unit: str) -> str | None:
    """'Euro cent' -> 'EUR', 'öre' -> 'SEK'; övriga -> None."""
    return CENT_UNITS.get(re.sub(r"\s+", "", unit).casefold())


def unit_factor(unit: str) -> tuple[Decimal, str] | None:
    """'MSEK' -> (1 000 000, 'SEK'); 'SEK' -> (1, 'SEK'); 'Euro cent' -> (0,01, 'EUR');
    övriga (procent, ton …) -> None."""
    if unit in CURRENCIES:
        return Decimal(1), unit
    if cent_currency(unit):
        return Decimal("0.01"), cent_currency(unit)
    for prefix in ("MD", "T", "M"):
        if unit.startswith(prefix) and unit[len(prefix):] in CURRENCIES:
            return SCALES[prefix], unit[len(prefix):]
    return None


# --- Period ------------------------------------------------------------------

_MONTHS = {
    "januari": 1, "jan": 1, "january": 1, "februari": 2, "feb": 2, "february": 2, "mars": 3, "mar": 3, "march": 3,
    "april": 4, "apr": 4, "maj": 5, "may": 5, "juni": 6, "jun": 6, "june": 6, "juli": 7, "jul": 7, "july": 7,
    "augusti": 8, "aug": 8, "august": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_M = "|".join(sorted(_MONTHS, key=len, reverse=True))
_RANGE_RE = re.compile(
    rf"(?:\d{{1,2}}\s+)?\b({_M})\b\.?\s*(?:20\d\d\s*)?[-–—]\s*(?:\d{{1,2}}\s+)?\b({_M})\b", re.IGNORECASE)
_QUARTER_RE = re.compile(
    r"(första|andra|tredje|fjärde) kvartalet|kvartal(et)? [1-4]\b|\bkvartalet\b|\bq[1-4]\b|"
    r"(first|second|third|fourth) quarter|\bthe quarter\b",
    re.IGNORECASE)
_YTD_RE = re.compile(
    r"nio månader|niomånaders|sex månader|första halvåret|delåret|hela perioden|first (nine|six) months|"
    r"first half|year[- ]to[- ]date|the period",
    re.IGNORECASE)
_YEAR_RE = re.compile(r"helåret|full[- ]year|räkenskapsåret", re.IGNORECASE)


def period_before(text: str, pos: int) -> str | None:
    """Sorts period ('kvartal', 'delår', 'helår') för närmaste periodangivelse före pos."""
    window_start = max(0, pos - PERIOD_LOOKBACK_CHARS)
    window = text[window_start:pos]
    found: list[tuple[int, str]] = []
    for m in _RANGE_RE.finditer(window):
        a, b = _MONTHS[m.group(1).lower()], _MONTHS[m.group(2).lower()]
        span = (b - a) % 12 + 1
        found.append((m.end(), "kvartal" if span == 3 else "helår" if span == 12 else "delår"))
    found += [(m.end(), "kvartal") for m in _QUARTER_RE.finditer(window)]
    found += [(m.end(), "delår") for m in _YTD_RE.finditer(window)]
    found += [(m.end(), "helår") for m in _YEAR_RE.finditer(window)]
    return max(found)[1] if found else None


# --- Kontroll av ett nyckeltal ------------------------------------------------


def _in_quote(value: float, numbers, percent: bool = False) -> bool:
    v = abs(Decimal(str(value)))
    return any(abs(n.value) == v and (n.percent or not percent) for n in numbers)


# Justerat resultat per aktie: citatet ska säga att måttet är justerat.
ADJUSTED_KPI = "justerat-resultat-per-aktie"
ADJUSTED_RE = re.compile(
    r"justera|underliggande|jämförelsestörande|engångspost|exklusive|exkl\.|rensat|"
    r"adjusted|underlying|items affecting comparability|excluding|non-recurring",
    re.IGNORECASE,
)


def check_kpi(k: dict, kind: str, raw_text: str, where: str, res: Result, language: str | None = None) -> None:
    """Kontrollerar ett nyckeltal mot rapporttexten (raw_text: normaliserade mellanslag). language: rapportens
    språk ur rådatans index; "en" läser citatets tal med engelskt skrivsätt ("1,234.5" = 1 234,5)."""
    quote = _normalize_ws(k["quote"])
    if len(quote.split()) > MAX_QUOTE_WORDS:
        res.errors.append(f"{where}: citatet har fler än {MAX_QUOTE_WORDS} ord")
    if k["id"] == ADJUSTED_KPI and not ADJUSTED_RE.search(quote):
        res.errors.append(f"{where}: citatet säger inte att resultatet per aktie är justerat")
    pos = raw_text.find(quote)
    if pos < 0:
        res.errors.append(f"{where}: citatet finns inte i rapporttexten: {k['quote']!r}")
    numbers = extract_numbers(quote, language)
    if not _in_quote(k["value"], numbers):
        res.errors.append(f"{where}: värdet {k['value']} finns inte i citatet")
    if k["previous_value"] is None and k["change_pct"] is None:
        res.errors.append(f"{where}: varken föregående års värde eller förändring anges")
    if k["previous_value"] is not None and not _in_quote(k["previous_value"], numbers):
        res.errors.append(f"{where}: föregående års värde {k['previous_value']} finns inte i citatet")
    if k["change_pct"] is not None:
        if kind == "procent":
            res.errors.append(f"{where}: förändring i procent anges inte för procenttal (jämför värdena)")
        elif not _in_quote(k["change_pct"], numbers, percent=True):
            res.errors.append(f"{where}: förändringen {k['change_pct']} % finns inte i citatet")
        elif k["previous_value"] not in (None, 0):
            computed = (k["value"] / k["previous_value"] - 1) * 100
            if abs(abs(computed) - abs(k["change_pct"])) > CHANGE_TOLERANCE_PP:
                res.errors.append(f"{where}: förändringen {k['change_pct']} % stämmer inte med värdena "
                                  f"({computed:.1f} %)")
    if k["unit"] not in UNIT_KINDS[kind] and not (kind == "per_aktie" and cent_currency(k["unit"])):
        res.errors.append(f"{where}: enheten {k['unit']!r} passar inte nyckeltalets typ {kind!r}")
    if pos >= 0:
        # Perioden avgörs av närmaste angivelse före värdet – även inne i citatet, t.ex.
        # "+12,93 (+7,45) för hela perioden och för kvartalet till -1,13 (+1,70)".
        value_at = next((quote.find(n.raw) for n in numbers
                         if abs(n.value) == abs(Decimal(str(k["value"]))) and quote.find(n.raw) >= 0), 0)
        found = period_before(raw_text, pos + value_at)
        if found is None and kind == "per_aktie" and k["period_kind"] == "kvartal":
            # Per aktie visas mot samma kvartal i fjol ("Mot fjol"): perioden måste vara belagd som kvartalet,
            # så att årets första nio månader aldrig jämförs som ett kvartal.
            res.errors.append(f"{where}: ingen kvartalsangivelse (t.ex. \"juli–september\", \"tredje kvartalet\") "
                              "före citatet")
        elif found is None:
            res.warnings.append(f"{where}: ingen periodangivelse hittades före citatet")
        elif found != k["period_kind"]:
            res.errors.append(f"{where}: citatet står under en {found}sangivelse men anges som {k['period_kind']}")


def _kpi_kinds(instruments: dict[str, Instrument]) -> dict[str, dict[str, str]]:
    """ticker -> {nyckeltal-id: typ} enligt report_list.kpis_for."""
    from report_list import kpis_for

    sector_kpis = load_sector_kpis()
    return {t: {k["id"]: k["kind"] for k in kpis_for(i, sector_kpis)} for t, i in instruments.items()}


HIGHLIGHT_FROM = date(2026, 10, 26)  # "I rapporten" krävs för rapporter från och med vecka 44
MAX_HIGHLIGHT_WORDS = 15
MAX_HIGHLIGHT_QUOTES = 3


def highlight_required(scope: str) -> bool:
    """Om rapporterna i läget (vecka, dag eller uppdatering) ska ha "I rapporten" (highlight)."""
    start = iso_week_period(scope)[0] if re.fullmatch(r"\d{4}-W\d{2}", scope) else date.fromisoformat(scope[:10])
    return start >= HIGHLIGHT_FROM


# Ord som en mening inte slutar på: står de sist har raden kapats mitt i ("… bland annat inom.").
DANGLING_WORDS = {
    "och", "samt", "men", "eller", "att", "som", "med", "för", "till", "av", "på", "i", "inom", "om", "från", "mot",
    "bland", "annat", "under", "efter", "vid", "genom", "trots", "utan", "än", "den", "det", "de", "en", "ett",
    "är", "var", "har", "hade", "blev", "kommer", "ska", "kan", "väntas", "både", "dels", "vilket", "där", "när",
}


def sentence_problems(text: str) -> list[str]:
    """Formella tecken på att raden inte är en hel mening: stor bokstav (eller siffra) först, punkt,
    utrops- eller frågetecken sist, inget "…" och inget ord sist som kräver en fortsättning. Om
    meningen är grammatiskt fullständig i övrigt går inte att avgöra här."""
    t = text.strip()
    out = []
    if not t or not (t[0].isupper() or t[0].isdigit()):
        out.append("ska börja med stor bokstav")
    if t.endswith(("…", "...")) or not t.endswith((".", "!", "?")):
        out.append("ska sluta med punkt")
    words = re.findall(r"\w+", t.rstrip(".!?…").casefold())  # med tal: "… under 2027." slutar på 2027
    if words and words[-1] in DANGLING_WORDS:
        out.append(f"slutar på {words[-1]!r}")
    if t.count("(") != t.count(")") or t.count("\"") % 2 or t.count("”") % 2:
        out.append("parentes eller citattecken stängs inte")
    return out


TONES = ("positiv", "blandad", "neutral", "negativ")


def check_highlight(h: dict, raw_text: str, where: str, res: Result, require_tone: bool = True,
                    language: str | None = None) -> None:
    """"I rapporten": en hel mening (sentence_problems, högst MAX_HIGHLIGHT_WORDS ord) om det rapporten själv lyfter fram, med
    ordagranna citat som för nyheterna: varje tal i raden ska beläggas exakt av ett citat, riktningsord får
    inte motsäga citaten, och inga ord om aktien, kursen eller råd."""
    text, quotes = h["text"], h["quotes"]
    for problem in sentence_problems(text):
        res.errors.append(f"{where}: inte en hel mening ({problem}): {text!r}")
    if len(text.split()) > MAX_HIGHLIGHT_WORDS:
        res.errors.append(f"{where}: raden har {len(text.split())} ord (max {MAX_HIGHLIGHT_WORDS})")
    if not 1 <= len(quotes) <= MAX_HIGHLIGHT_QUOTES:
        res.errors.append(f"{where}: {len(quotes)} citat (1–{MAX_HIGHLIGHT_QUOTES})")
    for q in quotes:
        if len(q.split()) > MAX_QUOTE_WORDS:
            res.errors.append(f"{where}: citatet har {len(q.split())} ord (max {MAX_QUOTE_WORDS}): {q[:60]!r}…")
        if _normalize_ws(q) not in raw_text:
            res.errors.append(f"{where}: citatet finns inte i rapporttexten: {q[:60]!r}")
    missing = unsupported_numbers(text, quotes, language)
    if missing:
        res.errors.append(f"{where}: siffror utan exakt stöd i citaten: {missing}")
    conflicts = direction_conflicts(text, quotes, language)
    if conflicts:
        res.errors.append(f"{where}: riktningen motsäger citaten: {conflicts}")
    forbidden = sorted({m.group(0) for m in _SENTIMENT_FORBIDDEN_RE.finditer(text)})
    if forbidden:
        res.errors.append(f"{where}: ord om aktien, kursen eller råd: {forbidden}")
    # Tonen ("Ton i rapporten") bygger på raden och dess citat, som nyheternas sentiment: raden ska dela
    # innehållsord med citaten.
    if require_tone and h.get("tone") not in TONES:
        res.errors.append(f"{where}: tonen saknas eller är okänd ({h.get('tone')!r}; tillåtna: {', '.join(TONES)})")
    if quotes and not _content_stems(text) & _content_stems(" ".join(quotes)):
        res.errors.append(f"{where}: raden delar inget innehållsord med citaten")


def _check_report(r: dict, listed: dict, kinds: dict[str, str], where: str, res: Result,
                  scope: str | None = None) -> None:
    from report_list import report_path

    if r["raw_file"] != listed["raw_file"]:
        res.errors.append(f"{where}: raw_file {r['raw_file']!r} är inte rapporten {listed['raw_file']!r}")
        return
    path = report_path(listed)
    if not path.is_file():
        res.errors.append(f"{where}: rapporttexten {path} finns inte")
        return
    raw_text = _normalize_ws(path.read_text(encoding="utf-8"))
    language = listed.get("language")
    if r.get("highlight"):
        check_highlight(r["highlight"], raw_text, f"{where} I rapporten", res,
                        require_tone=bool(scope) and highlight_required(scope), language=language)
    elif scope and highlight_required(scope) and r["kpis"]:  # ett utdrag utan siffror behöver ingen rad
        res.errors.append(f"{where}: \"I rapporten\" (highlight) saknas")
    seen: set[str] = set()
    for k in r["kpis"]:
        kw = f"{where} {k['id']}"
        if k["id"] not in kinds:
            res.errors.append(f"{kw}: nyckeltalet hör inte till bolaget ({', '.join(kinds)})")
            continue
        if k["id"] in seen:
            res.errors.append(f"{kw}: dubblett")
        seen.add(k["id"])
        check_kpi(k, kinds[k["id"]], raw_text, kw, res, language)
    if not r["kpis"] and not r.get("note"):
        res.errors.append(f"{where}: inga nyckeltal och ingen förklaring (note)")


def _listing(scope: str, instruments: dict[str, Instrument], dirs: dict) -> list[dict]:
    from report_list import list_scope

    return list_scope(scope, instruments, load_sector_kpis(), **dirs)


def _dirs(raw_dir: Path, morning_raw: Path | None, results_dir: Path | None) -> dict:
    return {"raw_dir": raw_dir, "morning_raw": morning_raw, "results_dir": results_dir}


@dataclass
class BatchCheck:
    """Valideringen av en omgångsfil uppdelad per rapport. Fel i `errors` gäller hela filen (schema, fel
    omgång) och då går ingen rapport att använda; annars underkänns bara rapporterna i `rejected`
    (ticker -> fel), och övriga rapporter i filen är godkända."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(default_factory=dict)
    listed: dict[str, dict] = field(default_factory=dict)

    def result(self) -> Result:
        res = Result()
        res.errors = self.errors + [e for errs in self.rejected.values() for e in errs]
        res.warnings = list(self.warnings)
        return res


def check_batch(data: dict, week: str, batch: int, instruments: dict[str, Instrument],
                raw_dir: Path = RAW_DIR, morning_raw: Path | None = None,
                results_dir: Path | None = None) -> BatchCheck:
    """Som validate_batch, men med felen per rapport (se BatchCheck)."""
    from report_list import MORNING_RAW, RESULTS_DIR

    dirs = _dirs(raw_dir, morning_raw or MORNING_RAW, results_dir or RESULTS_DIR)
    out = BatchCheck()
    out.errors += _schema_errors(_schema_validator("results-batch.schema.json"), data)
    if out.errors:
        return out
    if data["week"] != week or data["batch"] != batch:
        out.errors.append(f"week/batch ({data['week']}, {data['batch']}) stämmer inte med filen ({week}, {batch})")
        return out
    out.listed = listed = {r["ticker"]: r for r in _listing(week, instruments, dirs) if r["batch"] == batch}
    got = [r["ticker"] for r in data["reports"]]
    for t in sorted(set(listed) - set(got)):
        out.rejected[t] = [f"{t}: rapporten saknas i omgången"]
    for t in sorted(set(got) - set(listed)):
        # Ett bolag som inte står i listan tas aldrig med; övriga rapporter påverkas inte.
        out.rejected[t] = [f"{t}: inte en rapport i omgång {batch} enligt report_list.py"]
    for t in sorted({t for t in got if got.count(t) > 1} & set(listed)):
        out.rejected[t] = [f"{t}: samma bolag förekommer flera gånger"]
    kinds = _kpi_kinds(instruments)
    for r in data["reports"]:
        t = r["ticker"]
        if t not in listed or t in out.rejected:
            continue
        res = Result()
        _check_report(r, listed[t], kinds[t], t, res, week)
        out.warnings += res.warnings
        if res.errors:
            out.rejected[t] = res.errors
    return out


def validate_batch(data: dict, week: str, batch: int, instruments: dict[str, Instrument],
                   raw_dir: Path = RAW_DIR, morning_raw: Path | None = None,
                   results_dir: Path | None = None) -> Result:
    """En omgångsfil från /rapportutfall: data/results/<vecka>/omgang-<N>.json eller
    data/results/morning/<datum>/omgang-<N>.json (week är då datumet). Godkänd bara om alla
    rapporter är godkända; results.py använder check_batch och tar med de godkända."""
    return check_batch(data, week, batch, instruments, raw_dir, morning_raw, results_dir).result()


def scope_period(scope: str) -> dict:
    """{start, end} för en vecka, en dag eller en uppdatering (dess datum)."""
    if re.fullmatch(r"\d{4}-W\d{2}", scope):
        start, end = iso_week_period(scope)
        return {"start": start.isoformat(), "end": end.isoformat()}
    return {"start": scope[:10], "end": scope[:10]}


def validate_results(data: dict, week: str, instruments: dict[str, Instrument], raw_dir: Path = RAW_DIR,
                     morning_raw: Path | None = None, results_dir: Path | None = None,
                     consensus_dir: Path | None = DATA_DIR / "consensus") -> Result:
    """Den sammanslagna filen från scripts/results.py: data/results/<vecka>.json eller
    data/results/morning/<datum>.json. Varje rapport valideras mot listan för sitt eget
    läge (scope): veckans fil innehåller också morgonkörningarnas rapporter."""
    from report_list import MORNING_RAW, RESULTS_DIR

    dirs = _dirs(raw_dir, morning_raw or MORNING_RAW, results_dir or RESULTS_DIR)
    res = Result()
    res.errors += _schema_errors(_schema_validator("results.schema.json"), data)
    if res.errors:
        return res
    if data["week"] != week or data["period"] != scope_period(week):
        res.errors.append("week/period stämmer inte med filnamnet")
    _check_datetime(data["generated_at"], "generated_at", res)
    listings: dict[str, dict[str, dict]] = {}
    kinds = _kpi_kinds(instruments)
    for r in data["reports"]:
        where = f"{r['ticker']} ({r['scope']})"
        if r["scope"] != week and not (re.fullmatch(r"\d{4}-W\d{2}", week) and scope_period(week)["start"]
                                       <= r["scope"][:10] <= scope_period(week)["end"]):
            res.errors.append(f"{where}: läget {r['scope']} hör inte till {week}")
            continue
        if r["scope"] not in listings:
            listings[r["scope"]] = {x["ticker"]: x for x in _listing(r["scope"], instruments, dirs)}
        listed = listings[r["scope"]]
        if r["ticker"] not in listed:
            res.errors.append(f"{where}: inte en rapport i {r['scope']} enligt report_list.py")
            continue
        inst = instruments[r["ticker"]]
        if r["name"] != inst.namn or r["url"] != listed[r["ticker"]]["url"]:
            res.errors.append(f"{where}: namn eller url stämmer inte med listan")
        _check_report(r, listed[r["ticker"]], kinds[r["ticker"]], where, res, r["scope"])
        for k in r["kpis"]:
            _check_yoy(k, kinds[r["ticker"]].get(k["id"]), f"{where} {k['id']}", res)
        _check_comparison(r, where, res, consensus_dir)
    approved = {(r["ticker"], r["scope"]) for r in data["reports"]}
    for r in data.get("rejected", []):
        if r["ticker"] not in instruments or (r["ticker"], r["scope"]) in approved:
            res.errors.append(f"{r['ticker']} ({r['scope']}): underkänd men okänd eller också godkänd")
    return res


def _check_yoy(k: dict, kind: str | None, where: str, res: Result) -> None:
    """yoy_pct = rapportens procentsats, annars (värde − fjol) / |fjol|, inte för procenttal."""
    if k["change_pct"] is not None:
        expected = k["change_pct"]
    elif kind == "procent" or k["previous_value"] in (None, 0):
        expected = None
    else:
        expected = round((k["value"] - k["previous_value"]) / abs(k["previous_value"]) * 100, 1)
    if k["yoy_pct"] != expected:
        res.errors.append(f"{where}: yoy_pct {k['yoy_pct']} ≠ {expected}")


IN_LINE_PCT = 2.0  # inom ±2 % av förväntan räknas som "i linje"


def surprise_pct(actual: float, expected: float) -> float:
    """Avvikelse från förväntan i procent av förväntans belopp (rätt tecken även om förväntan är negativ)."""
    return (actual - expected) / abs(expected) * 100


def verdict(surprise: float) -> str:
    return "i linje" if abs(surprise) <= IN_LINE_PCT else "över" if surprise > 0 else "under"


# Samma regel som varningen "stor förändring, få analytiker" i src/lib/consensus.ts: förväntad
# förändring mot fjol över ±50 % från färre än 3 analytiker. Har bolagets konsensus den varningen
# (för vinst per aktie eller omsättning) bedöms utfallet inte som över/under.
BIG_CHANGE_PCT = 50.0
MIN_ANALYSTS = 3
UNCERTAIN = "osäker konsensus"


def expected_change(estimate: dict) -> float | None:
    """Förväntad förändring mot fjol i procent; None om fjolåret saknas, är noll eller har annat tecken."""
    ya = estimate.get("year_ago")
    if not ya or (ya > 0) != (estimate["avg"] > 0):
        return None
    return (estimate["avg"] - ya) / abs(ya) * 100


def big_change_few_analysts(estimate: dict | None) -> bool:
    if not estimate or not estimate.get("avg"):
        return False
    change = expected_change(estimate)
    return change is not None and abs(change) > BIG_CHANGE_PCT and (estimate.get("analysts") or 0) < MIN_ANALYSTS


def uncertain_consensus(company: dict) -> bool:
    """Sant om bolagets konsensus har varningen "stor förändring, få analytiker"."""
    return big_change_few_analysts(company.get("eps")) or big_change_few_analysts(company.get("revenue"))


# Bedömningar utan procent mot förväntan (utredning W43: Volvo Car −1,63 mot −0,14 kr, Electrolux
# och Volati med bolagshändelse och teckenbyte, Green Landscaping teckenbyte, Hexagon +126 % mot
# ett annat resultatmått än Yahoos):
CORPORATE = "bolagshändelse"  # känd bolagshändelse under året (data/corporate_actions.json)
SIGN_CHANGE = "teckenbyte"    # utfall och förväntan har olika tecken
NEAR_ZERO = "nära noll"       # det ena värdet är mindre än NEAR_ZERO_RATIO av det andra
CHECK = "kontrollera"         # avvikelse över ±CHECK_PCT % utan någon av förklaringarna ovan
NEAR_ZERO_RATIO = 0.1
CHECK_PCT = 50.0
CORPORATE_ACTIONS = DATA_DIR / "corporate_actions.json"


def corporate_event_tickers(path: Path | None = None) -> set[str]:
    """Bolag med en känd bolagshändelse under året: utredda händelser (investigated) och justerade
    händelser (actions med factor ≠ 1). Verkliga kursrörelser (factor 1) och nynoteringar
    (listings) påverkar inte jämförelsen med förväntan och räknas inte."""
    path = path or CORPORATE_ACTIONS
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return ({e["ticker"] for e in data.get("investigated", [])}
            | {e["ticker"] for e in data.get("actions", []) if e.get("factor", 1.0) != 1.0})


def comparison_verdict(actual: float, expected: float, surprise: float, uncertain: bool, corporate: bool) -> str:
    """Bedömningen för utfall mot förväntan, i ordning: bolagshändelse, teckenbyte, nära noll,
    osäker konsensus, kontrollera (över ±CHECK_PCT %), annars över/i linje/under."""
    if corporate:
        return CORPORATE
    if actual != 0 and expected != 0 and (actual > 0) != (expected > 0):
        return SIGN_CHANGE
    small, big = sorted((abs(actual), abs(expected)))
    if big > 0 and small < NEAR_ZERO_RATIO * big:
        return NEAR_ZERO
    if uncertain:
        return UNCERTAIN
    if abs(surprise) > CHECK_PCT:
        return CHECK
    return verdict(surprise)


def eps_for_comparison(by_id: dict[str, dict], currency: str | None) -> tuple[dict | None, str]:
    """Resultat per aktie som jämförs med förväntan: det justerade när rapporten anger ett för
    kvartalet i förväntans valuta, annars det redovisade. Returnerar (nyckeltal, "justerat"/"redovisat")."""
    adj = by_id.get(ADJUSTED_KPI)
    if adj and adj["period_kind"] == "kvartal" and (unit_factor(adj["unit"]) or (None, None))[1] == (currency or ""):
        return adj, "justerat"
    return by_id.get("resultat-per-aktie"), "redovisat"


def _check_comparison(r: dict, where: str, res: Result, consensus_dir: Path | None) -> None:
    """Utfall mot förväntan: räknas om ur nyckeltalet och den sparade konsensusen. Osäker
    konsensus kontrolleras mot konsensusfilen när den finns lokalt."""
    cmp = r.get("vs_consensus")
    if not cmp:
        return
    uncertain = cmp.get("uncertain", False)
    snap = consensus_dir / f"{cmp['consensus_date']}.json" if consensus_dir else None
    if snap and snap.exists():
        company = next((c for c in json.loads(snap.read_text(encoding="utf-8"))["companies"]
                        if c["ticker"] == r["ticker"]), None)
        if company is not None and uncertain_consensus(company) != uncertain:
            res.errors.append(f"{where}: uncertain={uncertain} stämmer inte med konsensus {cmp['consensus_date']}")
    corporate = cmp.get("corporate_event", False)
    if corporate != (r["ticker"] in corporate_event_tickers()):
        res.errors.append(f"{where}: corporate_event={corporate} stämmer inte med data/corporate_actions.json")
    by_id = {k["id"]: k for k in r["kpis"]}
    for key, kpi_id in (("eps", "resultat-per-aktie"), ("revenue", "nettoomsattning")):
        c = cmp.get(key)
        if not c:
            continue
        if key == "eps":
            k, basis = eps_for_comparison(by_id, c["currency"])
            kpi_id = k["id"] if k else kpi_id
            if c.get("basis", "redovisat") != basis:
                res.errors.append(f"{where}: eps jämförs med {c.get('basis', 'redovisat')} men ska använda {basis}")
        else:
            k = by_id.get(kpi_id)
        if k is None or k["period_kind"] != "kvartal":
            res.errors.append(f"{where}: jämförelse {key} utan kvartalsvärde för {kpi_id}")
            continue
        factor = unit_factor(k["unit"])
        if factor is None or factor[1] != c["currency"]:
            res.errors.append(f"{where}: {key} jämförs i fel valuta eller enhet")
            continue
        actual = float(Decimal(str(k["value"])) * factor[0])
        if abs(actual - c["actual"]) > max(1e-6, abs(actual) * 1e-9):
            res.errors.append(f"{where}: {key} utfall {c['actual']} ≠ {actual}")
        surprise = surprise_pct(actual, c["expected"])
        if abs(surprise - c["surprise_pct"]) > 0.051:
            res.errors.append(f"{where}: {key} avvikelse {c['surprise_pct']} % ≠ {surprise:.1f} %")
        if c["verdict"] != comparison_verdict(actual, c["expected"], c["surprise_pct"], uncertain, corporate):
            res.errors.append(f"{where}: {key} bedömningen {c['verdict']!r} stämmer inte med avvikelsen")


def batch_status(path: Path, instruments: dict[str, Instrument]) -> str:
    """Läget för en omgångsfil (data/results/…/<läge>/omgang-<N>.json), för run_results.ps1:
    "godkänd", "hela" (saknas eller går inte att använda alls: gör om hela omgången) eller de
    underkända rapporternas tickrar, kommaseparerade (gör om bara dem)."""
    if not path.exists():
        return "hela"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return "hela"
    check = check_batch(data, path.parent.name, int(path.stem.split("-")[1]), instruments)
    if check.errors:
        return "hela"
    return ",".join(sorted(check.rejected)) or "godkänd"


if __name__ == "__main__":
    import sys

    from validate import load_instruments

    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 3 or sys.argv[1] != "--status":
        print("Användning: python scripts/validate_results.py --status <omgångsfil>", file=sys.stderr)
        sys.exit(2)
    print(batch_status(Path(sys.argv[2]), load_instruments()))

