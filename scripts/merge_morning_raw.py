"""Kompletterar veckans rådata i det privata repot med poster från morgonflödets lokala hämtning.

GitHub Actions hämtar råtexten varannan timme men hoppar över eller försenar körningar (5–14 timmars
luckor vecka 43). Morgon- och eftermiddagsflödet hämtar lokalt till data/morning/raw/<vecka>/ (MFN med
500 poster), och där fanns 46 av veckans 68 Large Cap-rapporter som saknades i data/raw/<vecka>/ –
bland annat Tele2, Telia, SEB, Swedbank, Volvo och ABB. Veckosammanfattningen läser bara
data/raw/<vecka>/, så de kom aldrig med.

Skriptet lägger till poster som finns i morgonrådatan men inte i veckans rådata (samma källa och id,
eller samma rubrik och publiceringsminut), kopierar textfilerna, skriver ett sammanslaget index.json
och committar till veckans-finans-raw (som säkerhetskopiorna, scripts/backup_week.commit_files).
Befintliga poster ändras aldrig. Indexet får en rad under "merged_from_morning".

Körs av run_weekly.ps1 före /sammanfatta-veckan, och av morning.py innan en vecka rensas ur
data/morning/raw/ (complete): då försvinner inget som bara fanns lokalt.

Användning:
    python scripts/merge_morning_raw.py 2026-W43            # kompletterar och committar
    python scripts/merge_morning_raw.py 2026-W43 --torrkorning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from backup_week import commit_files, git  # noqa: E402
from validate import RAW_DIR  # noqa: E402

MORNING_RAW = ROOT / "data" / "morning" / "raw"
STOCKHOLM = ZoneInfo("Europe/Stockholm")
WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def _key(it: dict) -> str:
    return f"{it.get('source')}:{it.get('id') or it.get('url')}"


def _title_key(it: dict, owner: str | None) -> tuple[str, str, str]:
    """Rubrik, publiceringsminut och avsändare. Utan avsändaren togs olika bolags rapporter med samma rubrik samma
    minut för dubbletter (2026-10-23 07:30: Addnodes och Indutrades "Delårsrapport 1 januari – 30 september 2026",
    Beijer Almas, Bufabs och Lifcos "Delårsrapport januari-september 2026"), och tre rapporter kom aldrig med."""
    return (" ".join((it.get("title") or "").lower().split()), (it.get("published") or "")[:16], owner or "")


def _owner_fn(instruments: dict | None):
    """Avsändaren för en post: tickern enligt rapportkalendern (samma för MFN:s och Cisions slug för samma bolag),
    annars postens egen slug."""
    from report_calendar import match_company, name_patterns
    from validate import load_instruments

    inst = instruments if instruments is not None else load_instruments()
    patterns = name_patterns(inst)
    return lambda it: match_company(it, inst, patterns) or it.get("company") or ""


def merge(week_raw: Path, morning_week: Path, now: datetime, instruments: dict | None = None) -> tuple[dict, list[dict]]:
    """Det sammanslagna indexet och de poster som lades till (bara från morgonrådatan)."""
    index = json.loads((week_raw / "index.json").read_text(encoding="utf-8"))
    morning = json.loads((morning_week / "index.json").read_text(encoding="utf-8"))
    owner = _owner_fn(instruments)
    items = index.get("items", [])
    keys = {_key(it) for it in items}
    titles = {_title_key(it, owner(it)) for it in items}
    also = {a.get("url") for it in items for a in it.get("also_in", [])}
    paths = {it.get("path") for it in items}
    added = []
    for it in morning.get("items", []):
        tk = _title_key(it, owner(it))
        if _key(it) in keys or tk in titles or it.get("url") in also or it.get("path") in paths:
            continue
        if not (morning_week / it["path"]).is_file():
            continue
        added.append(it)
        keys.add(_key(it))
        titles.add(tk)
        paths.add(it["path"])
    if added:
        index["items"] = sorted(items + added, key=lambda it: it.get("published") or "")
        index.setdefault("merged_from_morning", []).append(
            {"time": now.isoformat(timespec="seconds"), "items": len(added)})
    return index, added


def complete(week: str, raw_dir: Path = RAW_DIR, morning_raw: Path = MORNING_RAW, dry_run: bool = False,
             instruments: dict | None = None) -> tuple[bool, str, int]:
    """Kompletterar veckan i rådatarepot med morgonrådatans poster som saknas där, och committar.
    (lyckades, meddelande, antal tillagda). Saknar rådatarepot veckan helt skapas den inte här (ok = False),
    så att morgonrådatan inte rensas bort utan kopia."""
    week_raw, morning_week = raw_dir / week, morning_raw / week
    if not (morning_week / "index.json").exists():
        return True, f"Ingen komplettering: {morning_week} saknar index.json.", 0
    if not (week_raw / "index.json").exists():
        return False, f"{week_raw} saknar index.json – kan inte kopiera morgonens poster dit.", 0
    if not dry_run:
        # Räkna på senaste versionen (Actions kan ha pushat), så att inga av dess poster skrivs över.
        pull = git(raw_dir, "pull", "--rebase", "-q")
        if pull.returncode != 0:
            return False, f"git pull misslyckades, ingen komplettering: {pull.stderr.strip()}", 0
    index, added = merge(week_raw, morning_week, datetime.now(STOCKHOLM), instruments)
    msg = f"Komplettering {week}: {len(added)} poster från morgonens lokala hämtning saknades i rådatarepot"
    if not added or dry_run:
        return True, msg, len(added)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_index = Path(tmp) / "index.json"
        tmp_index.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files = [(morning_week / it["path"], f"{week}/{it['path']}") for it in added]
        files.append((tmp_index, f"{week}/index.json"))
        ok, cmsg = commit_files(raw_dir, files, f"Komplettering {week}: {len(added)} poster från morgonens hämtning")
    # commit_files räknar upp varje fil; för hundratals filer räcker commit och antal.
    return ok, f"{msg}. Säkerhetskopia: {cmsg.split(' (')[0]} ({len(added)} textfiler och index.json)", len(added)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("week")
    p.add_argument("--torrkorning", action="store_true", help="visa bara vad som skulle läggas till")
    args = p.parse_args(argv)
    if not WEEK_RE.match(args.week):
        print(f"ogiltig vecka: {args.week!r}", file=sys.stderr)
        return 1
    ok, msg, _ = complete(args.week, dry_run=args.torrkorning)
    print(msg, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
