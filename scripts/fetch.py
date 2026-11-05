"""Hämtar veckans råtext från MFN, Cision och Riksbanken till <raw-dir>/<vecka>/.

Körs varannan timme i GitHub Actions i det privata repot veckans-finans-raw
(se deploy/raw-repo/). Varje körning gör exakt ett anrop per källa, med timeout
och tydlig User-Agent. Poster samlas på hög över veckan: en post som redan finns
skrivs inte om.

Utdata per vecka (ISO-vecka räknad i Europe/Stockholm utifrån postens egen
publiceringstid, inte körningens):
    <vecka>/index.json            metadata per post + logg över körningar
    <vecka>/mfn/<id>.txt          fulltext
    <vecka>/cision/<id>.txt       utdrag ur RSS (Cision har bara utdrag i flödet)
    <vecka>/riksbanken/<id>.txt   styrräntan (nyckeltal): värde, föregående värde, ändringsdatum;
                                  samma värden strukturerat i index.json → data.policy_rate

Dubbletter mellan MFN och Cision (samma pressmeddelande via båda) slås ihop:
MFN-versionen behålls eftersom den har fulltext, och Cision-länken sparas i
also_in. MFN:s svenska och engelska versioner av samma meddelande (samma
bolag, samma minut) slås också ihop – den svenska behålls.

Bara standardbiblioteket används (tzdata krävs dock på Windows för zoneinfo).

Användning:
    python scripts/fetch.py [--raw-dir data/raw]
Exit-kod: 0 = minst en källa OK (fallerade källor loggas och, i GitHub Actions,
skrivs som ::warning::), 1 = alla källor fallerade.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STOCKHOLM = ZoneInfo("Europe/Stockholm")

USER_AGENT = "veckans-finans-fetch/0.1 (+https://github.com/peternordengren-netizen/veckans-finans)"
TIMEOUT_S = 30

# Kontrollerade 2026-10-02. MFN:s RSS och limit-parametern är odokumenterade
# men fungerar; limit=200 täcker ungefär ett dygn. Cisions flöde täcker ungefär
# 7 timmar och kan inte pagineras – därav körning varannan timme (GitHub hoppar ibland
# över eller försenar schemalagda körningar).
# På tunga rapportdagar räcker 200 poster bara några timmar bakåt (2026-10-20 kl. 15: till
# 08:15). Morgonflödet, som ska nå tillbaka till förra morgonrapporten, använder därför
# --mfn-limit 500 – fortfarande ett anrop (500 gav 249 svenska poster).
MFN_LIMIT = 200
MFN_URL = f"https://mfn.se/all/s/nordic.rss?limit={MFN_LIMIT}"
CISION_URL = "https://news.cision.com/se/ListItems?format=rss"
# Bolag som bara publicerar i Cisions internationella nyhetsrum (t.ex. Gentoo Media) syns inte i den svenska listan.
# Deras egna flöden hämtas, ett anrop per bolag, för bolag med cision_slug i instruments.csv. Adressen är den som
# RSS-ikonen på bolagets Cision-sida öppnar (kontrollerat för Gentoo 2026-11-05). Cision erbjuder själv prenumeration
# på ett visst bolags nyheter "by email and RSS" (news.cision.com/_editorial/about); robots.txt spärrar inget.
CISION_COMPANY_URL = "https://news.cision.com/{slug}/ListItems?format=rss"
CISION_COMPANY_MAX_AGE_DAYS = 14
# Styrräntan: dagliga observationer för ett datumintervall (…/<från>/<till>),
# kontrollerat 2026-10-05. Fem år bakåt räcker för att hitta senaste ändringen
# (den förra låg ett år bakåt) och är ett enda litet anrop.
RIKSBANK_SERIES = "SECBREPOEFF"
RIKSBANK_URL = f"https://api.riksbank.se/swea/v1/Observations/{RIKSBANK_SERIES}/"
RIKSBANK_LOOKBACK_DAYS = 5 * 366
# Riksbankens webbsida om styrräntan – källänken som visas för läsaren (inte API:et).
RIKSBANK_PAGE_URL = "https://www.riksbank.se/sv/statistik/rantor-och-valutakurser/styrranta-in--och-utlaningsranta/"
# Räntebesluten: Riksbankens RSS för pressmeddelanden (från sidan "Prenumerera på utskick", kontrollerat
# 2026-11-05). Beslut som ännu inte börjat gälla syns inte i API:ets serie. Dagen beslutet tillämpas står bara
# i själva pressmeddelandet ("Beslutet om styrräntan tillämpas från den 11 november 2026."); det hämtas en gång
# per nytt beslut (inte om beslutets id redan finns i rådatan).
RIKSBANK_RSS_URL = "https://www.riksbank.se/sv/rss/pressmeddelanden/"
DECISION_TITLE_RE = re.compile(
    r"^Styrräntan (?:(?P<dir>höjs|sänks) till|(?P<same>oförändrad) på) (?P<value>\d+(?:,\d+)?) procent", re.IGNORECASE)
DECISION_SENTENCE_RE = re.compile(r"Direktionen har (?:därför )?beslutat att [^.]*styrräntan[^.]*\.")
EFFECTIVE_RE = re.compile(r"Beslutet om styrräntan tillämpas från (?:och med )?den (\d{1,2}) (\w+) (\d{4})")
SV_MONTHS = {m: i for i, m in enumerate(["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
                                          "september", "oktober", "november", "december"], start=1)}
DECISION_MAX_AGE_DAYS = 60  # äldre beslut har redan börjat gälla och syns i API:ets serie

MFN_NS = "{https://mfn.se/schemas/rss-ns-x/}"
# Scope SE sparas alltid. Andra scope (CA, DK, FI, IS …) bara för bolag i instruments.csv: t.ex. Lundin
# Gold och Lundin Mining publicerar med scope CA och föll tidigare bort (kontrollerat 2026-11-05).
MFN_SCOPES = {"SE"}
INSTRUMENTS_CSV = ROOT / "data" / "instruments.csv"
# Bolagssuffix i MFN:s och Cisions slugar ("dometic-group", "sampo-oyj"); samma regel som rapportkalendern.
SLUG_SUFFIXES = {"", "ab", "publ", "ab-publ", "group", "holding", "asa", "oyj", "plc"}
MFN_LANGUAGES = {"sv", "en"}
MFN_SKIP_TAGS = {"sub:ci:insider"}  # insynshandel och flaggningar sparas inte

# Rutinmeddelanden sparas men taggas "rutin" så att sammanfattningen kan välja bort dem.
# MFN: på taggprefix eller rubrik (alla MFN-poster är inte taggade). Cision saknar
# taggar, så där används bara rubriken.
MFN_ROUTINE_TAG_PREFIXES = ("sub:ci:gm", "sub:ca:shares", "sub:ci:presentation")
_INVITATION = r"^\s*inbjudan\b|^\s*invitation\b|inbjudan till|invitation to"
ROUTINE_TITLE_RE = re.compile(
    # Stämmor, nyhetsbrev, antal aktier
    r"kallelse till|kommuniké från|antal aktier och röster|nyhetsbrev"
    r"|notice of|bulletin from|number of shares and votes|newsletter"
    # Återköp av egna aktier
    r"|återköp av|aktieåterköp|förvärv av egna"
    r"|repurchases? of|share repurchases?|buy-?backs? of|share buy-?backs?|acquisitions? of own"
    r"|\brepurchase\b.*\b(?:week|vecka)\s+\d+"  # veckorapport om återköp ("Repurchase B shares in week 40")
    # Inbjudningar till rapportpresentationer
    r"|" + _INVITATION + r"|webbsändning|telefonkonferens|webcast|teleconference|conference call",
    re.IGNORECASE,
)
# Själva rapporterna är inte rutin, även om rubriken nämner t.ex. en webbsändning –
# bara uttryckliga inbjudningar till en rapport räknas som rutin.
REPORT_TITLE_RE = re.compile(
    r"delårsrapport|kvartalsrapport|halvårsrapport|bokslutskommuniké|årsredovisning"
    r"|interim report|quarterly report|half-year report|year-end report|annual report",
    re.IGNORECASE,
)
INVITATION_RE = re.compile(_INVITATION, re.IGNORECASE)


def is_routine(title: str, tags: list[str] = ()) -> bool:
    """Rutinmeddelande enligt MFN-taggar eller rubrik. Rapporter är aldrig rutin
    (utom inbjudningar till dem)."""
    if REPORT_TITLE_RE.search(title) and not INVITATION_RE.search(title):
        return False
    return any(t.startswith(MFN_ROUTINE_TAG_PREFIXES) for t in tags) or bool(ROUTINE_TITLE_RE.search(title))


def retag_routine(entries: list[dict]) -> int:
    """Räknar om rutinmärkningen för befintliga poster (så att ändrade mönster
    även gäller tidigare hämtat). Returnerar antal ändrade poster."""
    changed = 0
    for e in entries:
        tags = [t for t in e.get("tags", []) if t != "rutin"]
        routine = e["source"] in ("mfn", "cision") and is_routine(e["title"], tags)
        new_tags = tags + (["rutin"] if routine else [])
        if routine != e.get("routine") or new_tags != e.get("tags"):
            e["routine"], e["tags"] = routine, new_tags
            changed += 1
    return changed

# Poster från olika källor räknas som samma pressmeddelande om rubriken är lika
# (efter normalisering) och publiceringstiderna ligger så här nära varandra.
DEDUP_WINDOW = timedelta(minutes=15)
SOURCE_PRIORITY = {"mfn": 0, "cision": 1}  # lägre vinner


@dataclass
class Item:
    """En post i normaliserad form, oberoende av källa."""

    id: str
    source: str
    source_name: str
    url: str
    published: datetime  # tidszonsmedveten
    title: str
    company: str | None
    language: str | None
    tags: list[str]
    text: str
    text_kind: str  # "fulltext" | "utdrag" | "data"
    routine: bool = False
    data: dict | None = None  # strukturerade värden (nyckeltal), sparas i index.json
    feed: str | None = None  # källans namn i körningsloggen (t.ex. "cision-gentoo-media-inc"), sparas inte


@dataclass
class RunResult:
    items: list[Item] = field(default_factory=list)
    log: dict[str, dict] = field(default_factory=dict)

    @property
    def failed(self) -> list[str]:
        return [name for name, r in self.log.items() if r["status"] != "ok"]


# --- Tid ---------------------------------------------------------------------


def iso_week(dt: datetime) -> str:
    """ISO-vecka för tidpunkten i svensk tid, t.ex. '2026-W40'.

    Spelar roll runt midnatt mellan söndag och måndag: måndag 00:30 svensk tid
    (söndag 22:30 UTC sommartid) hör till den nya veckan, fast det fortfarande
    är söndag i UTC.
    """
    if dt.tzinfo is None:
        raise ValueError("tidpunkten saknar tidszon")
    year, week, _ = dt.astimezone(STOCKHOLM).isocalendar()
    return f"{year}-W{week:02d}"


def _parse_rfc822(value: str) -> datetime:
    dt = parsedate_to_datetime(value)
    if dt.tzinfo is None:  # RFC 822 utan zon tolkas som UTC
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt


# --- Parsning ----------------------------------------------------------------


def _slug_from_path(url: str, marker: str) -> str | None:
    """Bolagsslug ur länken: mfn.se/a/<slug>/… , mfn.se/cis/a/<slug>/… , news.cision.com/se/<slug>/r/…"""
    parts = [p for p in urlparse(url).path.split("/") if p]
    try:
        return parts[parts.index(marker) + 1]
    except (ValueError, IndexError):
        return None


def slugify(name: str) -> str:
    """'Dometic Group' -> 'dometic-group', 'Bergman & Beving' -> 'bergman-beving' (som MFN:s och Cisions slugar)."""
    s = name.casefold().translate(str.maketrans("åäöéü", "aaoeu"))
    return "-".join(re.findall(r"[a-z0-9]+", s))


def slug_matches(slug: str, base: str) -> bool:
    """Sant om sluggen är namnets slug, eventuellt följd av ett bolagssuffix ("-ab", "-group" …)."""
    return bool(base) and (slug == base or (slug.startswith(base + "-") and slug[len(base) + 1:] in SLUG_SUFFIXES))


@dataclass(frozen=True)
class Watched:
    """Bevakade bolag i instruments.csv: mfn_slug exakt, och namn/alias som slug (med bolagssuffix)."""

    slugs: frozenset[str] = frozenset()
    bases: frozenset[str] = frozenset()

    def __contains__(self, slug: object) -> bool:
        return isinstance(slug, str) and (slug in self.slugs or any(slug_matches(slug, b) for b in self.bases))


def load_watched(path: Path = INSTRUMENTS_CSV) -> Watched:
    """Bevakade bolag ur instruments.csv (bara standardbiblioteket, så att det fungerar i GitHub Actions).
    Saknas filen bevakas inga bolag, och bara scope SE sparas från MFN."""
    if not path.exists():
        return Watched()
    slugs, bases = set(), set()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("mfn_slug") or "").strip():
                slugs.add(row["mfn_slug"].strip())
            for n in [row.get("namn") or "", *(row.get("alias") or "").split("|")]:
                if len(n.strip()) >= 3:
                    bases.add(slugify(n.strip()))
    return Watched(frozenset(slugs), frozenset(bases - {""}))


_watched: Watched | None = None


def default_watched() -> Watched:
    global _watched
    if _watched is None:
        _watched = load_watched()
    return _watched


def parse_mfn(data: bytes, watched: Watched | None = None) -> list[Item]:
    """MFN:s poster med scope SE, och med andra scope bara för bevakade bolag (watched, standard:
    instruments.csv)."""
    watched = default_watched() if watched is None else watched
    items = []
    for el in ET.fromstring(data).findall("./channel/item"):
        scope = el.findtext(MFN_NS + "scope")
        lang = el.findtext(MFN_NS + "language")
        tags = [t.text for t in el.findall(MFN_NS + "tag") if t.text]
        url = el.findtext("link") or ""
        company = _slug_from_path(url, "a")
        if scope not in MFN_SCOPES and company not in watched:
            continue
        if lang not in MFN_LANGUAGES or MFN_SKIP_TAGS & set(tags):
            continue
        text_el = next((c for c in el.findall(MFN_NS + "content") if c.get("type") == "text"), None)
        text = (text_el.text if text_el is not None else None) or el.findtext("description") or ""
        title = (el.findtext("title") or "").strip()
        routine = is_routine(title, tags)
        items.append(Item(
            id=el.findtext(MFN_NS + "newsId") or url,
            source="mfn",
            source_name="MFN",
            url=url,
            published=_parse_rfc822(el.findtext("pubDate") or ""),
            title=title,
            company=company,
            language=lang,
            tags=tags + (["rutin"] if routine else []),
            text=text.strip(),
            text_kind="fulltext",
            routine=routine,
        ))
    return items


def parse_cision(data: bytes) -> list[Item]:
    items = []
    for el in ET.fromstring(data).findall("./channel/item"):
        url = el.findtext("link") or ""
        title = html.unescape((el.findtext("title") or "").strip())
        routine = is_routine(title)
        items.append(Item(
            id=el.findtext("guid") or url,
            source="cision",
            source_name="Cision",
            url=url,
            published=_parse_rfc822(el.findtext("pubDate") or ""),
            title=title,
            company=_slug_from_path(url, "se"),
            language=None,
            tags=["rutin"] if routine else [],
            text=html.unescape(el.findtext("description") or "").strip(),
            text_kind="utdrag",
            routine=routine,
        ))
    return items


def sv_number(value: float) -> str:
    """1.75 -> '1,75', 2.0 -> '2' (samma format som i veckofilens texter)."""
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def policy_rate_figure(observations: list[dict]) -> dict:
    """Nyckeltal ur dagliga observationer (äldst först): senaste värdet, värdet
    före senaste ändringen och datumet då nuvarande nivå började gälla.
    previous_value/changed_on är None om ingen ändring finns i intervallet."""
    if not observations:
        raise ValueError("inga observationer")
    obs = sorted(observations, key=lambda o: o["date"])
    latest = obs[-1]
    previous_value = changed_on = None
    for older, newer in zip(reversed(obs[:-1]), reversed(obs[1:])):
        if older["value"] != latest["value"]:
            previous_value, changed_on = older["value"], newer["date"]
            break
    return {"value": latest["value"], "date": latest["date"], "previous_value": previous_value, "changed_on": changed_on}


def parse_riksbank(data: bytes) -> list[Item]:
    """Styrräntan blir ett nyckeltal: en post per observationsdatum, med
    strukturerade värden i Item.data och en mening per värde i texten."""
    fig = policy_rate_figure(json.loads(data))
    day = fig["date"]
    text = f"Riksbankens styrränta (serie {RIKSBANK_SERIES}) var {sv_number(fig['value'])} procent den {day}."
    if fig["previous_value"] is not None:
        text += (f" Föregående värde var {sv_number(fig['previous_value'])} procent."
                 f" Nuvarande nivå gäller sedan {fig['changed_on']}.")
    return [Item(
        id=f"styrranta-{day}",
        source="riksbanken",
        source_name="Riksbanken",
        url=RIKSBANK_PAGE_URL,
        published=datetime.fromisoformat(day).replace(tzinfo=STOCKHOLM),
        title=f"Riksbankens styrränta {day}",
        company=None,
        language="sv",
        tags=["nyckeltal", "styrränta"],
        text=text,
        text_kind="data",
        data={"policy_rate": fig},
    )]


def _html_text(data: bytes) -> str:
    """Löptext ur en HTML-sida (taggar bort, entiteter avkodade, blanksteg sammanslagna)."""
    s = data.decode("utf-8", "replace")
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return " ".join(html.unescape(s).split())


def parse_riksbank_decision(data: bytes, get: Callable[[str], bytes], known_ids: set[str],
                            today: date) -> list[Item]:
    """Senaste räntebeslutet ur Riksbankens RSS för pressmeddelanden, med dagen det tillämpas (ur
    pressmeddelandet). Tomt om inget beslut finns de senaste DECISION_MAX_AGE_DAYS dagarna eller om beslutet
    redan finns i rådatan (då hämtas inte pressmeddelandet igen)."""
    root = ET.fromstring(data)
    decisions = []
    for el in root.iter("item"):
        title = (el.findtext("title") or "").strip()
        m = DECISION_TITLE_RE.match(title)
        if m:
            decisions.append((parsedate_to_datetime(el.findtext("pubDate")).astimezone(STOCKHOLM), title, m,
                              (el.findtext("link") or "").strip()))
    if not decisions:
        return []
    published, title, m, link = max(decisions, key=lambda d: d[0])
    decided_on = published.date()
    item_id = f"styrrantebeslut-{decided_on}"
    if item_id in known_ids or (today - decided_on).days > DECISION_MAX_AGE_DAYS:
        return []
    page = _html_text(get(link))
    eff = EFFECTIVE_RE.search(page)
    if not eff or eff.group(2).lower() not in SV_MONTHS:
        raise ValueError(f"hittar inte dagen beslutet tillämpas i {link}")
    effective = date(int(eff.group(3)), SV_MONTHS[eff.group(2).lower()], int(eff.group(1)))
    sentence = DECISION_SENTENCE_RE.search(page)
    value = float(m.group("value").replace(",", "."))
    decision = {"value": value, "decided_on": decided_on.isoformat(), "effective_from": effective.isoformat(),
                "change": "oförändrad" if m.group("same") else m.group("dir").lower(), "url": link}
    text = " ".join(x for x in (title + ".", sentence.group(0) if sentence else "", eff.group(0) + ".") if x)
    return [Item(
        id=item_id,
        source="riksbanken",
        source_name="Riksbanken",
        url=link,
        published=published,
        title=title,
        company=None,
        language="sv",
        tags=["nyckeltal", "styrräntebeslut"],
        text=text,
        text_kind="utdrag",
        data={"policy_decision": decision},
    )]


parse_riksbank_decision.needs_context = True  # får get, kända id:n och dagens datum

# --- Hämtning ----------------------------------------------------------------


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read()


def riksbank_url(today: date) -> str:
    """Ett anrop som täcker RIKSBANK_LOOKBACK_DAYS bakåt, så att senaste ändringen hittas."""
    return f"{RIKSBANK_URL}{today - timedelta(days=RIKSBANK_LOOKBACK_DAYS)}/{today}"


Source = tuple[str, str | Callable[[date], str], Callable[[bytes], list[Item]]]
SOURCES: list[Source] = [
    ("mfn", MFN_URL, parse_mfn),  # MFN först, så att den vinner dubbletter inom samma körning
    ("cision", CISION_URL, parse_cision),
    ("riksbanken", riksbank_url, parse_riksbank),
    ("riksbanken-beslut", RIKSBANK_RSS_URL, parse_riksbank_decision),
]


def cision_company_parser(slug: str) -> Callable:
    """Parser för ett bolags eget Cision-flöde: som Cisions svenska lista, men bolagets slug ur instruments.csv
    (länkarna saknar /se/) och bara poster från de senaste CISION_COMPANY_MAX_AGE_DAYS dagarna (flödet går
    månader bakåt; äldre veckor ska inte fyllas på i efterhand)."""

    def parse(data: bytes, _get: Callable, _known: set[str], today: date) -> list[Item]:
        items = parse_cision(data)
        for it in items:
            it.company = slug
        return [it for it in items if (today - it.published.astimezone(STOCKHOLM).date()).days <= CISION_COMPANY_MAX_AGE_DAYS]

    parse.needs_context = True
    return parse


def cision_company_sources(path: Path = INSTRUMENTS_CSV) -> list[Source]:
    """En källa per bolag med cision_slug i instruments.csv: news.cision.com/<slug>/ListItems?format=rss.
    Bara standardbiblioteket (körs i GitHub Actions). Saknas filen eller kolumnen blir listan tom."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        slugs = sorted({(row.get("cision_slug") or "").strip() for row in csv.DictReader(f)} - {""})
    return [(f"cision-{s}", CISION_COMPANY_URL.format(slug=s), cision_company_parser(s)) for s in slugs]


def fetch_all(sources: list[Source] = SOURCES, get: Callable[[str], bytes] = http_get,
              today: date | None = None, known_ids: set[str] | None = None) -> RunResult:
    """Ett anrop per källa (räntebeslutet: RSS, och pressmeddelandet en gång per nytt beslut). Fel loggas och
    övriga källor körs ändå."""
    res = RunResult()
    today = today or datetime.now(STOCKHOLM).date()
    for name, url, parse in sources:
        try:
            raw = get(url(today) if callable(url) else url)
            items = (parse(raw, get, known_ids or set(), today) if getattr(parse, "needs_context", False)
                     else parse(raw))
        except Exception as err:  # noqa: BLE001 – varje källfel ska loggas, inte stoppa körningen
            res.log[name] = {"status": "fel", "error": f"{type(err).__name__}: {err}"[:300]}
            print(f"[{name}] FEL: {err}", file=sys.stderr)
            continue
        for it in items:
            it.feed = name
        res.items += items
        res.log[name] = {"status": "ok", "items": len(items)}
        print(f"[{name}] {len(items)} poster")
    return res


# --- Lagring och dubbletter --------------------------------------------------


def normalize_title(title: str) -> set[str]:
    """Rubrikvarianter för dubblettjämförelse: hela rubriken och rubriken utan
    'Bolag: '-prefix (MFN skriver ofta 'Bolaget AB: Rubrik', Cision bara 'Rubrik')."""

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s).casefold()
        return " ".join(re.sub(r"[^\w]+", " ", s).split())

    variants = {norm(title)}
    if ":" in title:
        variants.add(norm(title.split(":", 1)[1]))
    variants.discard("")
    return variants


def _is_duplicate(a: dict, b: Item) -> bool:
    if a["source"] == b.source or b.source not in SOURCE_PRIORITY or a["source"] not in SOURCE_PRIORITY:
        return False
    close = abs(datetime.fromisoformat(a["published"]) - b.published) <= DEDUP_WINDOW
    return close and bool(normalize_title(a["title"]) & normalize_title(b.title))


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _text_file(item: Item) -> str:
    local = item.published.astimezone(STOCKHOLM)
    kind = {"utdrag": "utdrag (källans RSS innehåller bara början av texten)"}.get(item.text_kind, item.text_kind)
    return (
        f"Rubrik: {item.title}\n"
        f"Källa: {item.source_name}\n"
        f"Publicerad: {local:%Y-%m-%d %H:%M} (Europe/Stockholm)\n"
        f"URL: {item.url}\n"
        f"Text: {kind}\n"
        f"---\n"
        f"{item.text}\n"
    )


def _file_name(item_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", item_id)[:120] + ".txt"


LANG_PREFERENCE = {"sv": 0, "en": 1}  # lägre vinner


def merge_language_pairs(entries: list[dict], week_dir: Path) -> int:
    """Slår ihop MFN:s språkversioner av samma pressmeddelande och behåller den svenska.

    Ett par = två MFN-poster med samma bolagsslug och samma publiceringsminut,
    en på svenska och en på engelska. Den engelska tas bort (även filen) och
    noteras i den svenskas also_in. Om en minut har fler än en post per språk
    för samma bolag går det inte att para ihop säkert – då lämnas alla.
    Returnerar antal borttagna poster.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in entries:
        if e["source"] == "mfn" and e.get("company") and e.get("language") in LANG_PREFERENCE:
            groups.setdefault((e["company"], e["published"][:16]), []).append(e)
    removed = 0
    for group in groups.values():
        by_lang = {lang: [e for e in group if e["language"] == lang] for lang in LANG_PREFERENCE}
        if len(by_lang["sv"]) != 1 or len(by_lang["en"]) != 1:
            continue
        keep, drop = by_lang["sv"][0], by_lang["en"][0]
        (week_dir / drop["path"]).unlink(missing_ok=True)
        entries.remove(drop)
        also = keep.setdefault("also_in", [])
        also.append({"source": drop["source"], "url": drop["url"], "id": drop["id"], "language": drop["language"]})
        also.extend(a for a in drop.get("also_in", []) if a not in also)
        removed += 1
    return removed


def _already_merged(entries: list[dict], item: Item) -> bool:
    """Sant om posten tidigare slagits ihop in i en annan post (finns i någons also_in)."""
    return any(
        a.get("id") == item.id and a["source"] == item.source for e in entries for a in e.get("also_in", [])
    )


def _load_index(path: Path, week: str) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"week": week, "updated_at": None, "items": [], "runs": []}


def store(result: RunResult, raw_dir: Path, now: datetime) -> dict[str, int]:
    """Skriver nya poster till <raw_dir>/<vecka>/ och uppdaterar index.json.

    Returnerar antal nya poster per källa. Körningsloggen hamnar i indexet för
    den vecka körningen gjordes (svensk tid).
    """
    new_counts = {name: 0 for name in result.log}
    indexes: dict[str, dict] = {}

    def index_for(week: str) -> dict:
        if week not in indexes:
            indexes[week] = _load_index(raw_dir / week / "index.json", week)
        return indexes[week]

    ordered = sorted(result.items, key=lambda it: SOURCE_PRIORITY.get(it.source, 99))
    for item in ordered:
        week = iso_week(item.published)
        idx = index_for(week)
        entries = idx["items"]
        same = next((e for e in entries if e["id"] == item.id and e["source"] == item.source), None)
        if same is not None:
            # Nyckeltal (t.ex. styrräntan) uppdateras om datan ändrats eller saknas
            # (poster sparade i äldre format); vanliga nyheter skrivs aldrig om.
            if item.data is not None and same.get("data") != item.data:
                _write_atomic(raw_dir / week / same["path"], _text_file(item))
                same.update(data=item.data, url=item.url, tags=item.tags, title=item.title)
            continue
        if _already_merged(entries, item):
            continue
        dup = next((e for e in entries if _is_duplicate(e, item)), None)
        if dup is not None:
            if SOURCE_PRIORITY[dup["source"]] <= SOURCE_PRIORITY[item.source]:
                # Befintlig post vinner – notera bara att samma meddelande finns här också.
                also = dup.setdefault("also_in", [])
                if not any(a["url"] == item.url for a in also):
                    also.append({"source": item.source, "url": item.url})
                continue
            # Ny post vinner (MFN ersätter Cision-utdrag): ta bort den gamla.
            (raw_dir / week / dup["path"]).unlink(missing_ok=True)
            entries.remove(dup)
            carried = [{"source": dup["source"], "url": dup["url"]}, *dup.get("also_in", [])]
        else:
            carried = []

        rel = f"{item.source}/{_file_name(item.id)}"
        _write_atomic(raw_dir / week / rel, _text_file(item))
        entry = {
            "id": item.id,
            "source": item.source,
            "source_name": item.source_name,
            "url": item.url,
            "published": item.published.astimezone(STOCKHOLM).isoformat(),
            "title": item.title,
            "company": item.company,
            "language": item.language,
            "tags": item.tags,
            "routine": item.routine,
            "text_kind": item.text_kind,
            "path": rel,
        }
        if item.data is not None:
            entry["data"] = item.data
        if carried:
            entry["also_in"] = carried
        entries.append(entry)
        key = item.feed if item.feed in new_counts else item.source
        new_counts[key] = new_counts.get(key, 0) + 1

    run_week = iso_week(now)
    index_for(run_week)
    # Språkpar slås ihop i hela veckan varje körning, så att även äldre poster
    # (och par där den engelska kom först) städas.
    merged = sum(merge_language_pairs(idx["items"], raw_dir / week) for week, idx in indexes.items())
    for idx in indexes.values():
        retag_routine(idx["items"])
    run_log = {
        name: {**r, "new": new_counts.get(name, 0)} if r["status"] == "ok" else r
        for name, r in result.log.items()
    }
    indexes[run_week]["runs"].append({
        "time": now.astimezone(STOCKHOLM).isoformat(),
        "sources": run_log,
        "merged_language_pairs": merged,
    })

    for week, idx in indexes.items():
        idx["items"].sort(key=lambda e: e["published"])
        idx["updated_at"] = now.astimezone(STOCKHOLM).isoformat()
        _write_atomic(raw_dir / week / "index.json", json.dumps(idx, ensure_ascii=False, indent=2) + "\n")
    return new_counts


def known_ids(raw_dir: Path) -> set[str]:
    """Postid:n i rådatans index för de senaste veckorna (så att ett räntebeslut inte hämtas igen)."""
    out: set[str] = set()
    for p in sorted(raw_dir.glob("*-W*/index.json"))[-12:]:
        try:
            out |= {it["id"] for it in json.loads(p.read_text(encoding="utf-8")).get("items", [])}
        except (OSError, ValueError, KeyError):
            continue
    return out

def main(argv: list[str] | None = None, get: Callable[[str], bytes] = http_get, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    parser.add_argument("--mfn-limit", type=int, default=MFN_LIMIT,
                        help=f"antal poster i MFN:s flöde (standard {MFN_LIMIT}; morgonflödet 500)")
    args = parser.parse_args(argv)

    now = now or datetime.now(STOCKHOLM)
    sources = [(name, MFN_URL.replace(f"limit={MFN_LIMIT}", f"limit={args.mfn_limit}") if name == "mfn" else url, parse)
               for name, url, parse in SOURCES]
    sources += cision_company_sources(INSTRUMENTS_CSV)  # läses vid körning (testerna pekar om INSTRUMENTS_CSV)
    result = fetch_all(sources, get=get, today=now.astimezone(STOCKHOLM).date(), known_ids=known_ids(args.raw_dir))
    new = store(result, args.raw_dir, now)
    print("nya poster:", new)
    if len(result.failed) == len(result.log):
        return 1
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # Delvisa fel ska synas i Actions-loggen men inte göra körningen röd.
        for name in result.failed:
            print(f"::warning title=Källfel: {name}::{result.log[name]['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
