"""Dagens avsnitt av Handelsbankens podd Morgonrapporten, överst i morgonrapporten (titel och länk).

- Flödet: Spreaker-RSS:en som Apples podd-katalog anger för podden (kontrollerad 2026-11-11 via Apples öppna sök-API,
  id 1440456855). Det är poddens offentliga flöde för poddappar. En hämtning per morgonrapport (inte vid omförsöken
  med bara kurser), timeout och tydlig User-Agent.
- Flödet har titel, datum, länk och ljudfil per avsnitt, men inga beskrivningar. Bara titeln, länken och tiden sparas;
  ljudet hämtas aldrig.
- Dagens avsnitt: det senaste avsnittet publicerat i dag (Europe/Stockholm). Inget avsnitt i dag (t.ex. före ca
  06:15) eller ett fel ger null respektive ett felmeddelande; det påverkar inte morgonrapporten i övrigt.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

FEED_URL = "https://www.spreaker.com/show/2904244/episodes/feed"
SHOW = "Morgonrapporten (Handelsbanken)"
STOCKHOLM = ZoneInfo("Europe/Stockholm")
TIMEOUT = 20


def http_get(url: str) -> bytes:
    from fetch import USER_AGENT

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, */*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def episodes(xml: bytes) -> list[dict]:
    """Avsnitten i flödet: {"title", "url", "published"} (tid i Stockholm), nyast först som i flödet."""
    out = []
    for it in ET.fromstring(xml).iter("item"):
        title, link, pub = it.findtext("title"), it.findtext("link"), it.findtext("pubDate")
        if not (title and link and pub):
            continue
        try:
            when = parsedate_to_datetime(pub).astimezone(STOCKHOLM)
        except (TypeError, ValueError):
            continue
        out.append({"title": title.strip(), "url": link.strip(), "published": when.isoformat(timespec="minutes")})
    return out


def today_episode(xml: bytes, today: date) -> dict | None:
    """Det senaste avsnittet publicerat i dag, eller None."""
    todays = [e for e in episodes(xml) if datetime.fromisoformat(e["published"]).date() == today]
    return max(todays, key=lambda e: e["published"]) if todays else None


def for_morning(today: date, get=http_get) -> dict:
    """{"show", "feed", "episode": {...} | None, "error": str | None} för morgonrapporten (en hämtning)."""
    try:
        ep = today_episode(get(FEED_URL), today)
        return {"show": SHOW, "feed": FEED_URL, "episode": ep, "error": None}
    except Exception as exc:  # noqa: BLE001 - podden får aldrig stoppa morgonrapporten
        return {"show": SHOW, "feed": FEED_URL, "episode": None, "error": f"{type(exc).__name__}: {exc}"[:200]}
