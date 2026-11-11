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
QUOTE_WORDS = 8
MAX_ATTEMPTS = 3  # körningar (à två Claude-försök) per fil innan den hoppas över
SUFFIXES = {".pptx", ".pdf"}
FORBIDDEN = re.compile(r"\b(köp|köpa|sälj|sälja|kursmål|köprekommendation|säljrekommendation)\b", re.IGNORECASE)


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
    meta = {"id": fid, "file_name": path.name, "kind": kind, "kind_name": KINDS[kind], "file_date": file_date,
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
    return [t for t in out if t]


def check(s: dict, meta: dict, source: str, instruments: dict[str, Instrument], today: date) -> list[str]:
    """Fel i sammanfattningen (tom lista = godkänd), se modulbeskrivningen."""
    errors = []
    req = {"id": str, "kind": str, "title": str, "report_date": str, "headline": str, "points": list,
           "segments": list, "companies": list, "label": str, "source_url": str, "file_name": str}
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
    return check(s, meta, source, instruments or load_instruments(), today)


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
    staged = hb_dir / f"{fid}.json"
    staged.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok, msg = commit(raw_dir, [(staged, f"{RAW_SUBDIR}/{fid}.json")],
                     f"Handelsbanken: sammanfattning av {s['file_name']} ({s['report_date']}, manuellt tillagd)")
    if not ok:
        return False, f"kopian till rådatarepot misslyckades: {msg}"
    for p in (staged, work_dir / f"{fid}.txt", work_dir / f"{fid}.meta.json", work_dir / f"{fid}.attempts"):
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
