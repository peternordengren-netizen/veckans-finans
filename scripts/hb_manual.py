"""Handelsbankens rapporter som du laddar ner själv: Morgonrapporten (PowerPoint) och Fastighetsrapporten (PDF).

Handelsbanken Research (handelsbanken.researchonline.se) har ingen dokumenterad väg för automatisk hämtning, och
villkoren säger att rapporterna inte får mångfaldigas eller spridas utan skriftligt tillstånd. Därför:
- Du lägger filen i data/hb_manual/ (arbetskopian; driftkopian läser den via VF_HB_MANUAL_DIR, som FI-filerna).
- En ny fil (känd på innehållets SHA-256) sammanfattas en gång av Claude, med egna ord, utan citat, märkt
  "manuellt tillagd" och med länk till källsidan (scripts/run_hb.ps1 och .claude/commands/hb-sammanfatta.md).
- Bara sammanfattningen sparas, i rådatarepot (data/raw/hb/<id>.json). Filen och den utdragna texten hamnar aldrig i
  något repo: texten ligger i data/hb/work/ (git-ignorerad) bara medan sammanfattningen görs och tas sedan bort.

Steg (anropas av run_hb.ps1 vid morgonkörningen, Bollingerkontrollerna 09:20 och 13:20 och "Uppdatera nu"):
    python scripts/hb_manual.py --nya              # nya filer: texten till data/hb/work/<id>.txt, id:n på stdout
    python scripts/hb_manual.py --kontrollera <id> # Claude kör den här: kontrollerar data/hb/<id>.json mot texten
    python scripts/hb_manual.py --klar <id>        # godkänd: till rådatarepot, arbetsfilerna tas bort

Sammanfattningen har tre avsnitt:
a) "Rapporten i korthet": huvudbudskap, punkter, räntor, värderingar, delsegment och bolag som nämns, med egna ord.
b) "Betydelse för aktiemarknaden" ("impact"): berörda sektorer (data/sectors.json) och bolag (instruments.csv) med
   riktning (positiv, negativ eller blandad) och motivering. Märkt IMPACT_LABEL: Claudes tolkning, inte Handelsbankens.
c) "Vad vår data visar" ("our_data"): för de berörda bolagen värden ur sidans befintliga filer (company_data: senaste
   veckosignalerna, Kurs mot vinst och rapportkalendern), med Claudes kommentar och om rapportens bild och marknadens
   rörelse går isär ("diverges"). Värdena läggs på av det här skriptet när sammanfattningen sparas, inte av Claude,
   och varje tal i kommentaren ska finnas i bolagets värden (numbers_in_data).

Kontrollen (check):
- fälten finns och har rätt form; kind och datum stämmer med filen (Morgonrapporten: datumet i filnamnet, ÅÅMMDD)
- label är "manuellt tillagd" och source_url är källsidan för rapporttypen (SOURCE_URLS, inga gissade adresser)
- inga citat: ingen följd av QUOTE_WORDS ord i sammanfattningen finns i rapportens text, och inga citattecken
- bolagen finns i instruments.csv med samma namn
- inga ord som köp, sälj eller kursmål
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from validate import DATA_DIR, Instrument, load_instruments  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
MANUAL_DIR = Path(os.environ["VF_HB_MANUAL_DIR"]) if os.environ.get("VF_HB_MANUAL_DIR") else DATA_DIR / "hb_manual"
# Claude skriver sammanfattningen här (en riktig mapp i driftkopian, se scripts/setup_drift.ps1).
HB_DIR = DATA_DIR / "hb"
WORK_DIR = HB_DIR / "work"
RAW_SUBDIR = "hb"
KINDS = {"morgonrapport": "Morgonrapporten", "fastighetsrapport": "Fastighetsrapporten", "annan": "Handelsbanken-rapport"}
# Källsidorna (kontrollerade 2026-11-11): där rapporterna publiceras, inte filerna själva.
SOURCE_URLS = {
    "morgonrapport": "https://handelsbanken.researchonline.se/macro/publications/mgmote",
    "fastighetsrapport": "https://www.handelsbanken.se/sv/foretag/placera/fastighetsrapporten",
    "annan": "https://www.handelsbanken.se/sv/foretag/placera/marknadsinformation-och-analyser",
}
LABEL = "manuellt tillagd"
IMPACT_LABEL = "Claudes tolkning, inte Handelsbankens"
DIRECTIONS = {"positiv", "negativ", "blandad"}
# Värdena i avsnitt c), med förklaring (visas på sidan och skrivs i datafilen till Claude).
DATA_FIELDS = {
    "week_change_pct": "kursförändring senaste veckan, procent",
    "week_rel_index_pp": "kurs mot index senaste veckan, procentenheter",
    "ytd_pct": "kursförändring i år, procent",
    "ytd_rel_index_pp": "kurs mot index i år, procentenheter",
    "insider_net_sek": "insyn senaste veckan, nettoköp (+) eller nettoförsäljning (−) i kronor",
    "insider_buyers": "insyn, antal personer som köpt",
    "insider_sellers": "insyn, antal personer som sålt",
    "pe_now": "P/E nu",
    "pe_change_pct": "P/E-förändring i år, procent",
    "next_report": "kommande rapportdatum",
}
QUOTE_WORDS = 8
MAX_ATTEMPTS = 3  # körningar (à två Claude-försök) per fil innan den hoppas över
SUFFIXES = {".pptx", ".pdf"}
# Inga ord som köp eller sälj, i någon böjning (insyn beskrivs som förvärv och avyttring).
FORBIDDEN = re.compile(r"\b(köp|köpa|köper|köpte|köpt|köpts|köpare|köparna|sälj|sälja|säljer|sålde|sålt|sålts|säljare|"
                       r"säljarna|kursmål|köprekommendation|säljrekommendation)\b", re.IGNORECASE)
# "Går isär" (avsnitt c): riktningen i b) mot kursen mot index. Positiv men minst DIVERGE_YTD_PP sämre än index i år
# eller DIVERGE_WEEK_PP senaste veckan, eller negativ men lika mycket bättre. Blandad går aldrig isär.
DIVERGE_YTD_PP = 5.0
DIVERGE_WEEK_PP = 3.0


# --- Filer och text -------------------------------------------------------------------


def file_id(path: Path) -> str:
    """Innehållets SHA-256, 16 tecken: samma fil sammanfattas en gång, oavsett namn."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def extract_text(path: Path) -> str:
    """Texten i en PowerPoint (python-pptx: textrutor och tabeller, bild för bild) eller PDF (pypdf, sida för sida)."""
    if path.suffix.lower() == ".pptx":
        from pptx import Presentation

        parts = []
        for n, slide in enumerate(Presentation(str(path)).slides, start=1):
            lines = []
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                    lines += [p.text for p in shape.text_frame.paragraphs if p.text.strip()]
                if getattr(shape, "has_table", False) and shape.has_table:
                    for row in shape.table.rows:
                        lines.append(" | ".join(c.text.strip() for c in row.cells))
            parts.append(f"--- Bild {n} ---\n" + "\n".join(lines))
        return "\n\n".join(parts)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n\n".join(f"--- Sida {n} ---\n{(page.extract_text() or '').strip()}"
                           for n, page in enumerate(PdfReader(str(path)).pages, start=1))
    raise ValueError(f"okänt filformat: {path.name}")


def file_created(path: Path) -> str | None:
    """Filens eget skapandedatum (ÅÅÅÅ-MM-DD) ur metadatan: PDF:ens CreationDate (pypdf) eller PowerPointens
    core_properties.created. Används som rapportdatum när rapporten inte anger något exakt datum."""
    try:
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            d = (PdfReader(str(path)).metadata or {}).creation_date
        else:
            from pptx import Presentation

            d = Presentation(str(path)).core_properties.created
        return d.date().isoformat() if d else None
    except Exception:  # noqa: BLE001 - metadatan är valfri
        return None


def guess_kind(path: Path, text: str) -> tuple[str, str | None]:
    """(rapporttyp, datum ur filnamnet eller None). Morgonrapporten heter "ÅÅMMDD ….pptx" (t.ex. "261111 Final.pptx")."""
    m = re.match(r"(\d{2})(\d{2})(\d{2})\b", path.name)
    if path.suffix.lower() == ".pptx" and m:
        try:
            return "morgonrapport", date(2000 + int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            pass
    if "fastighet" in path.name.lower() or "fastighetsrapport" in text[:5000].lower():
        return "fastighetsrapport", None
    return "annan", None


def summarized_ids(raw_dir: Path) -> set[str]:
    """Id:n som redan har en sammanfattning i rådatarepot."""
    return {p.stem for p in (raw_dir / RAW_SUBDIR).glob("*.json")}


def new_files(manual_dir: Path, raw_dir: Path) -> list[tuple[str, Path]]:
    """(id, fil) för filer i manual_dir utan sammanfattning, äldst först."""
    done = summarized_ids(raw_dir)
    files = sorted((p for p in manual_dir.glob("*") if p.is_file() and p.suffix.lower() in SUFFIXES),
                   key=lambda p: p.stat().st_mtime)
    out, seen = [], set()
    for p in files:
        fid = file_id(p)
        if fid not in done and fid not in seen:
            out.append((fid, p))
            seen.add(fid)
    return out


def prepare(fid: str, path: Path, work_dir: Path = WORK_DIR) -> dict:
    """Texten till work_dir/<id>.txt och uppgifterna om filen till work_dir/<id>.meta.json (läses av Claude)."""
    text = extract_text(path)
    kind, file_date = guess_kind(path, text)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / f"{fid}.txt").write_text(text, encoding="utf-8")
    data = company_data(datetime.now(STOCKHOLM).date())
    (work_dir / f"{fid}.data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    meta = {"id": fid, "file_name": path.name, "kind": kind, "kind_name": KINDS[kind], "file_date": file_date,
            "file_created": file_created(path), "data": f"data/hb/work/{fid}.data.json",
            "source_url": SOURCE_URLS[kind], "label": LABEL, "out": f"data/hb/{fid}.json"}
    (work_dir / f"{fid}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def attempts(fid: str, work_dir: Path = WORK_DIR) -> int:
    p = work_dir / f"{fid}.attempts"
    return int(p.read_text()) if p.exists() else 0


def add_attempt(fid: str, work_dir: Path = WORK_DIR) -> int:
    n = attempts(fid, work_dir) + 1
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / f"{fid}.attempts").write_text(str(n))
    return n


# --- Avsnitt c): sidans befintliga data ----------------------------------------------------


def _latest(dir_: Path) -> Path | None:
    files = sorted(p for p in dir_.glob("*.json") if re.fullmatch(r"\d{4}-W\d{2}", p.stem))
    return files[-1] if files else None


def company_data(today: date, data_dir: Path = DATA_DIR) -> dict:
    """{"sources": {...}, "companies": {ticker: {DATA_FIELDS}}} ur sidans filer: senaste veckosignalerna
    (data/signals/<vecka>.json), senaste Kurs mot vinst (data/valuation/<vecka>.json) och rapportkalendern
    (data/calendar/*.json, första rapporten från och med today). Saknade värden är null."""
    out: dict[str, dict] = {}
    sources: dict = {"signals": None, "valuation": None, "calendar_from": today.isoformat()}

    def row(t: str, name: str) -> dict:
        return out.setdefault(t, {"name": name, **{k: None for k in DATA_FIELDS}})

    sig = _latest(data_dir / "signals")
    if sig:
        d = json.loads(sig.read_text(encoding="utf-8"))
        sources["signals"] = d.get("week")
        for c in d.get("companies", []):
            r = row(c["ticker"], c["name"])
            r.update({"week_change_pct": c.get("change_pct"), "week_rel_index_pp": c.get("relative_pp"),
                      "insider_net_sek": c.get("insider_net_sek"),
                      "insider_buyers": c.get("insider_buyers"), "insider_sellers": c.get("insider_sellers")})
    val = _latest(data_dir / "valuation")
    if val:
        d = json.loads(val.read_text(encoding="utf-8"))
        sources["valuation"] = d.get("week")
        for c in d.get("companies", []):
            r = row(c["ticker"], c["name"])
            r.update({"ytd_pct": c.get("ytd_pct"), "ytd_rel_index_pp": c.get("rel_index_pp"), "pe_now": c.get("pe_now"),
                      "pe_change_pct": c.get("pe_change_pct")})
    for p in sorted((data_dir / "calendar").glob("*.json")):
        try:
            events = json.loads(p.read_text(encoding="utf-8")).get("events", [])
        except ValueError:
            continue
        for e in events:
            if e.get("kind") == "rapport" and e.get("date", "") >= today.isoformat():
                r = row(e["ticker"], e["name"])
                if r["next_report"] is None or e["date"] < r["next_report"]:
                    r["next_report"] = e["date"]
    return {"sources": sources, "fields": DATA_FIELDS, "companies": out}


def _accepted_numbers(values: dict, extra: list[str] = ()) -> set[str]:
    """Skrivsätten för talen i ett bolags värden som får stå i en kommentar: med lagrade decimaler och avrundade till
    0–2 decimaler, utan tecken, med decimalkomma; kronbelopp också i tusental och miljoner."""
    ok: set[str] = set()
    for k, v in values.items():
        if k == "next_report" and isinstance(v, str):
            ok.update(_date_numbers(v))
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        a = abs(float(v))
        for d in (0, 1, 2):
            ok.add(f"{a:.{d}f}".replace(".", ","))
        ok.add(f"{a:g}".replace(".", ","))
        if k == "insider_net_sek":
            ok.add(str(int(round(a))))
            for d in (0, 1, 2):
                ok.add(f"{a / 1e6:.{d}f}".replace(".", ","))
                ok.add(f"{a / 1e3:.{d}f}".replace(".", ","))
    for e in extra:
        ok.update(_date_numbers(e))
    return {x for x in ok if x}


def _date_numbers(iso: str) -> set[str]:
    m = re.fullmatch(r"(\d{4})-W?(\d{2})(?:-(\d{2}))?", iso)
    if not m:
        return set()
    parts = {m[1], m[2], str(int(m[2]))}
    if m[3]:
        parts |= {m[3], str(int(m[3]))}
    return parts


def diverges(direction: str | None, values: dict) -> bool:
    """Går rapportens bild (riktningen i avsnitt b) och marknadens rörelse isär? Se DIVERGE_YTD_PP."""
    ytd, week = values.get("ytd_rel_index_pp"), values.get("week_rel_index_pp")
    if direction == "positiv":
        return (ytd is not None and ytd <= -DIVERGE_YTD_PP) or (week is not None and week <= -DIVERGE_WEEK_PP)
    if direction == "negativ":
        return (ytd is not None and ytd >= DIVERGE_YTD_PP) or (week is not None and week >= DIVERGE_WEEK_PP)
    return False


def numbers_in_data(note: str, values: dict, sources: dict) -> list[str]:
    """Tal i kommentaren som inte finns i bolagets värden (tom lista = alla finns). Datum i ISO-form räknas mot
    kommande rapportdatum och datakällornas veckor."""
    allowed = _accepted_numbers(values, [v for v in sources.values() if isinstance(v, str)])
    missing = []
    for iso in re.findall(r"\d{4}-\d{2}-\d{2}|\d{4}-W\d{2}", note):
        if iso != values.get("next_report") and iso not in sources.values():
            missing.append(iso)
    rest = re.sub(r"\d{4}-\d{2}-\d{2}|\d{4}-W\d{2}", " ", note)
    for tok in re.findall(r"\d+(?:[ \u00a0]\d{3})*(?:,\d+)?", rest):
        t = re.sub(r"[ \u00a0]", "", tok)
        if t not in allowed:
            missing.append(tok)
    return missing


# --- Kontrollen -------------------------------------------------------------------------


def _words(s: str) -> list[str]:
    return re.findall(r"[0-9a-zåäöéü]+(?:[.,][0-9]+)?", s.lower())


def quoted_runs(summary_texts: list[str], source: str, n: int = QUOTE_WORDS) -> list[str]:
    """Följder om n ord i sammanfattningen som också finns i källtexten (ord i följd, gemener)."""
    src = _words(source)
    grams = {tuple(src[i:i + n]) for i in range(len(src) - n + 1)}
    hits = []
    for t in summary_texts:
        w = _words(t)
        for i in range(len(w) - n + 1):
            if tuple(w[i:i + n]) in grams:
                hits.append(" ".join(w[i:i + n]))
                break
    return hits


def _texts(s: dict) -> list[str]:
    """Alla fritexter i en sammanfattning."""
    out = [s.get("title") or "", s.get("headline") or "", s.get("rates") or "", s.get("valuations") or ""]
    out += list(s.get("points") or [])
    out += [f"{x.get('name', '')} {x.get('view', '')}" for x in s.get("segments") or []]
    out += [x.get("note", "") for x in s.get("companies") or []]
    imp = s.get("impact") or {}
    out += [x.get("reason", "") for x in (imp.get("sectors") or []) + (imp.get("companies") or [])]
    out += [x.get("note", "") for x in s.get("our_data") or []]
    return [t for t in out if t]


def check(s: dict, meta: dict, source: str, instruments: dict[str, Instrument], today: date,
          data: dict | None = None, sector_ids: set[str] | None = None) -> list[str]:
    """Fel i sammanfattningen (tom lista = godkänd), se modulbeskrivningen. data: company_data för avsnitt c)."""
    errors = []
    req = {"id": str, "kind": str, "title": str, "report_date": str, "headline": str, "points": list,
           "segments": list, "companies": list, "label": str, "source_url": str, "file_name": str, "impact": dict,
           "our_data": list}
    for k, typ in req.items():
        if not isinstance(s.get(k), typ):
            errors.append(f"fältet {k!r} saknas eller har fel typ")
    if errors:
        return errors
    if s["id"] != meta["id"] or s["file_name"] != meta["file_name"]:
        errors.append("id eller filnamn stämmer inte med filen")
    if s["kind"] != meta["kind"]:
        errors.append(f"rapporttypen {s['kind']!r} ska vara {meta['kind']!r}")
    if s["label"] != LABEL:
        errors.append(f"märkningen ska vara {LABEL!r}")
    if s["source_url"] != SOURCE_URLS[meta["kind"]]:
        errors.append(f"länken ska vara källsidan {SOURCE_URLS[meta['kind']]}")
    try:
        rd = date.fromisoformat(s["report_date"])
        if rd > today:
            errors.append(f"rapportdatumet {rd} ligger i framtiden")
        if meta.get("file_date") and s["report_date"] != meta["file_date"]:
            errors.append(f"rapportdatumet {s['report_date']} ska vara {meta['file_date']} (filnamnet)")
    except ValueError:
        errors.append(f"rapportdatumet {s['report_date']!r} är inget datum (ÅÅÅÅ-MM-DD)")
    if not s["headline"].strip() or not 2 <= len(s["points"]) <= 8:
        errors.append("huvudbudskapet saknas, eller punkterna är färre än 2 eller fler än 8")
    if meta["kind"] == "fastighetsrapport":
        for k in ("rates", "valuations"):
            if not isinstance(s.get(k), str) or not s[k].strip():
                errors.append(f"fastighetsrapporten ska ha {k!r} (synen på räntor och värderingar)")
        if not s["segments"]:
            errors.append("fastighetsrapporten ska ha delsegmenten som nämns")
    for seg in s["segments"]:
        if not isinstance(seg, dict) or not seg.get("name") or not isinstance(seg.get("view"), str):
            errors.append(f"delsegmentet {seg!r} ska ha name och view")
    for c in s["companies"]:
        inst = instruments.get(c.get("ticker")) if isinstance(c, dict) else None
        if inst is None or inst.namn != c.get("name"):
            errors.append(f"bolaget {c!r} finns inte i instruments.csv med samma namn och ticker")
    errors += _check_impact_and_data(s, instruments, data or {"sources": {}, "companies": {}}, sector_ids)
    texts = _texts(s)
    if any(ch in t for t in texts for ch in '"“”«»'):
        errors.append("sammanfattningen innehåller citattecken (inga citat)")
    for run in quoted_runs(texts, source):
        errors.append(f"följd om {QUOTE_WORDS} ord ur rapporten (citat): {run!r}")
    for t in texts:
        m = FORBIDDEN.search(t)
        if m:
            errors.append(f"ordet {m.group(0)!r} ska inte användas (beskrivande sammanfattning)")
    return errors


def _check_impact_and_data(s: dict, instruments: dict[str, Instrument], data: dict,
                           sector_ids: set[str] | None) -> list[str]:
    """Avsnitt b) och c): märkningen, sektorerna och bolagen finns, riktningen, att c) har alla bolag i b), och att
    varje tal i c):s kommentarer finns i bolagets värden."""
    errors = []
    imp = s["impact"]
    if imp.get("label") != IMPACT_LABEL:
        errors.append(f"avsnitt b) ska vara märkt {IMPACT_LABEL!r}")
    secs, comps = imp.get("sectors"), imp.get("companies")
    if not isinstance(secs, list) or not isinstance(comps, list) or not (secs or comps):
        errors.append("avsnitt b) ska ha minst en berörd sektor eller ett berört bolag (sectors, companies)")
        return errors
    if sector_ids is None:
        from validate import load_sector_ids

        sector_ids = load_sector_ids()
    for x in secs:
        if not isinstance(x, dict) or x.get("sector") not in sector_ids:
            errors.append(f"sektorn {x!r} finns inte i data/sectors.json")
        elif x.get("direction") not in DIRECTIONS or not str(x.get("reason") or "").strip():
            errors.append(f"sektorn {x['sector']!r} ska ha riktning (positiv, negativ, blandad) och motivering")
    for x in comps:
        inst = instruments.get(x.get("ticker")) if isinstance(x, dict) else None
        if inst is None or inst.namn != x.get("name"):
            errors.append(f"bolaget {x!r} i avsnitt b) finns inte i instruments.csv med samma namn och ticker")
        elif x.get("direction") not in DIRECTIONS or not str(x.get("reason") or "").strip():
            errors.append(f"bolaget {x['ticker']!r} ska ha riktning (positiv, negativ, blandad) och motivering")
    od = s["our_data"]
    want = {x.get("ticker") for x in comps if isinstance(x, dict)}
    have = {x.get("ticker") for x in od if isinstance(x, dict)}
    if want - have:
        errors.append(f"avsnitt c) saknar bolagen i b): {sorted(t for t in want - have if t)}")
    for x in od:
        if not isinstance(x, dict) or x.get("ticker") not in instruments or instruments[x["ticker"]].namn != x.get("name"):
            errors.append(f"bolaget {x!r} i avsnitt c) finns inte i instruments.csv med samma namn och ticker")
            continue
        if not isinstance(x.get("diverges"), bool) or not isinstance(x.get("note"), str):
            errors.append(f"{x['ticker']} i avsnitt c) ska ha diverges (true/false) och note")
            continue
        values = (data.get("companies") or {}).get(x["ticker"]) or {}
        direction = next((c.get("direction") for c in comps if isinstance(c, dict) and c.get("ticker") == x["ticker"]), None)
        expected = diverges(direction, values)
        if x["diverges"] != expected:
            errors.append(f"{x['ticker']} i avsnitt c): diverges ska vara {str(expected).lower()} (riktning {direction}, "
                          f"mot index i år {values.get('ytd_rel_index_pp')}, senaste veckan {values.get('week_rel_index_pp')}; "
                          f"gränser {DIVERGE_YTD_PP:g} och {DIVERGE_WEEK_PP:g} procentenheter)")
        bad = numbers_in_data(x["note"], values, data.get("sources") or {})
        if bad:
            errors.append(f"{x['ticker']} i avsnitt c): talen {bad} finns inte i sidans data för bolaget")
    return errors


# --- Kommandon ------------------------------------------------------------------------


def cmd_new(manual_dir: Path, raw_dir: Path, work_dir: Path = WORK_DIR) -> list[str]:
    """Förbereder nya filer (högst MAX_ATTEMPTS körningar per fil) och returnerar deras id:n."""
    ids = []
    for fid, path in new_files(manual_dir, raw_dir):
        if attempts(fid, work_dir) >= MAX_ATTEMPTS:
            print(f"{path.name}: {MAX_ATTEMPTS} körningar utan godkänd sammanfattning - hoppar över "
                  f"(ta bort {work_dir / (fid + '.attempts')} för att försöka igen)", file=sys.stderr)
            continue
        try:
            meta = prepare(fid, path, work_dir)
        except Exception as exc:  # noqa: BLE001 - en trasig fil ska inte stoppa de andra
            print(f"{path.name}: texten kunde inte läsas ({type(exc).__name__}: {exc})", file=sys.stderr)
            continue
        print(f"{path.name}: {meta['kind_name']}, {len((work_dir / (fid + '.txt')).read_text(encoding='utf-8'))} "
              f"tecken text", file=sys.stderr)
        ids.append(fid)
    return ids


def cmd_check(fid: str, today: date, instruments: dict[str, Instrument] | None = None,
              hb_dir: Path = HB_DIR, work_dir: Path = WORK_DIR) -> list[str]:
    out = hb_dir / f"{fid}.json"
    if not out.exists():
        return [f"{out} finns inte"]
    try:
        s = json.loads(out.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{out} är ingen giltig JSON: {exc}"]
    meta = json.loads((work_dir / f"{fid}.meta.json").read_text(encoding="utf-8"))
    source = (work_dir / f"{fid}.txt").read_text(encoding="utf-8")
    data_path = work_dir / f"{fid}.data.json"
    data = json.loads(data_path.read_text(encoding="utf-8")) if data_path.exists() else None
    return check(s, meta, source, instruments or load_instruments(), today, data)


def cmd_done(fid: str, now: datetime, raw_dir: Path, hb_dir: Path = HB_DIR, work_dir: Path = WORK_DIR,
             commit=None) -> tuple[bool, str]:
    """Godkänd sammanfattning till rådatarepot (data/raw/hb/<id>.json), sedan tas arbetsfilerna bort."""
    errors = cmd_check(fid, now.date(), hb_dir=hb_dir, work_dir=work_dir)
    if errors:
        return False, "underkänd: " + "; ".join(errors[:5])
    if commit is None:
        from backup_week import commit_files as commit
    s = json.loads((hb_dir / f"{fid}.json").read_text(encoding="utf-8"))
    s["summarized_at"] = now.isoformat(timespec="seconds")
    # Avsnitt c): värdena ur sidans data läggs på här (inte av Claude), med källorna.
    data_path = work_dir / f"{fid}.data.json"
    data = json.loads(data_path.read_text(encoding="utf-8")) if data_path.exists() else {"sources": {}, "companies": {}}
    for x in s["our_data"]:
        vals = data["companies"].get(x["ticker"]) or {}
        x["data"] = {k: vals.get(k) for k in DATA_FIELDS}
    s["data_sources"] = data["sources"]
    staged = hb_dir / f"{fid}.json"
    staged.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok, msg = commit(raw_dir, [(staged, f"{RAW_SUBDIR}/{fid}.json")],
                     f"Handelsbanken: sammanfattning av {s['file_name']} ({s['report_date']}, manuellt tillagd)")
    if not ok:
        return False, f"kopian till rådatarepot misslyckades: {msg}"
    for p in (staged, work_dir / f"{fid}.txt", work_dir / f"{fid}.meta.json", work_dir / f"{fid}.attempts",
              work_dir / f"{fid}.data.json"):
        p.unlink(missing_ok=True)
    return True, f"{s['file_name']}: sammanfattad ({KINDS[s['kind']]} {s['report_date']}) -> rådatarepot: {msg}"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--nya", action="store_true", help="förbered nya filer, skriv deras id:n")
    g.add_argument("--kontrollera", metavar="ID", help="kontrollera data/hb/<id>.json mot rapportens text")
    g.add_argument("--klar", metavar="ID", help="godkänd sammanfattning till rådatarepot, ta bort arbetsfilerna")
    g.add_argument("--forsok", metavar="ID", help="räkna en körning för filen (run_hb.ps1)")
    args = p.parse_args(argv)
    now = datetime.now(STOCKHOLM)
    raw_dir = DATA_DIR / "raw"
    if args.nya:
        MANUAL_DIR.mkdir(parents=True, exist_ok=True)
        for fid in cmd_new(MANUAL_DIR, raw_dir):
            print(fid)
        return 0
    if args.forsok:
        print(add_attempt(args.forsok))
        return 0
    if args.kontrollera:
        errors = cmd_check(args.kontrollera, now.date())
        if errors:
            print("UNDERKÄND:", *errors, sep="\n  fel: ")
            return 1
        print("GODKÄND")
        return 0
    ok, msg = cmd_done(args.klar, now, raw_dir)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
