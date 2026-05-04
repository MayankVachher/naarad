"""News + weather + sun + on-this-day sources for the daily brief.

Toronto-anchored. Each fetch is network-bounded with per-call timeouts and
swallows individual failures so one bad source doesn't kill the brief.

Composition:
- World:    BBC World + The Guardian World
- Canada:   CBC Canada + Toronto Star
- AI/Tech:  Hacker News top + The Verge tech (Google-related items filtered out)
- Google:   blog.google + HN/Verge filtered for google|gemini|deepmind|alphabet
- Notable:  Wikipedia "on this day" (events + holidays)
- Weather:  Open-Meteo (no key)
- Sun:      astral (no network)

format_for_prompt() renders everything into a plain-text block to inject into
the Copilot prompt — Copilot then summarizes/rewrites the bullets in tone.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import feedparser
import httpx

log = logging.getLogger(__name__)

# ---- Source URLs ----

WORLD_FEEDS = [
    ("BBC World",  "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Guardian",   "https://www.theguardian.com/world/rss"),
    ("NYT World",  "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
]

CANADA_FEEDS = [
    ("CBC",            "https://www.cbc.ca/cmlink/rss-topstories"),
    ("Global News",    "https://globalnews.ca/feed/"),
    ("National Post",  "https://nationalpost.com/feed/"),
    ("CityNews TO",    "https://toronto.citynews.ca/feed/"),
]

TECH_FEEDS = [
    ("Hacker News",  "https://hnrss.org/frontpage?points=200"),
    ("The Verge",    "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("TechCrunch",   "https://techcrunch.com/feed/"),
]

GOOGLE_BLOG = ("Google Blog", "https://blog.google/rss/")

# Phrases that mark a headline as Google-relevant.
GOOGLE_KEYWORDS = re.compile(
    r"\b(google|gemini|deepmind|alphabet|pixel|workspace|youtube|bard|chrome)\b",
    re.IGNORECASE,
)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WIKI_ONTHISDAY_URL = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/{mm:02d}/{dd:02d}"

USER_AGENT = "naarad/0.1.0 (+https://github.com/MayankVachher/naarad)"

PER_FETCH_TIMEOUT = 12    # seconds (some Canadian feeds are slow)
MAX_PER_FEED = 5
MAX_NOTABLE = 5

# Open-Meteo weather code -> short human description.
WMO_DESCRIPTIONS = {
    0: "clear",
    1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


@dataclass
class Headline:
    source: str
    title: str
    link: str = ""


@dataclass
class BriefContext:
    location_name: str = ""
    weather_line: str = ""
    sunrise: str = ""
    sunset: str = ""
    world: list[Headline] = field(default_factory=list)
    canada: list[Headline] = field(default_factory=list)
    ai_tech: list[Headline] = field(default_factory=list)
    google: list[Headline] = field(default_factory=list)
    notable: list[str] = field(default_factory=list)


# ---------- RSS ----------

def _fetch_feed(url: str, source: str, max_items: int = MAX_PER_FEED) -> list[Headline]:
    """Fetch one RSS feed via httpx + feedparser. Returns [] on any error.

    Using httpx for the network layer (so we get a real timeout) and feeding
    bytes into feedparser; feedparser's own URL fetcher has no timeout knob.
    """
    try:
        with httpx.Client(timeout=PER_FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get(url, follow_redirects=True)
            resp.raise_for_status()
            content = resp.content
    except Exception as exc:
        log.warning("feed fetch failed: %s (%s) — %s", source, url, exc)
        return []

    try:
        parsed = feedparser.parse(content)
    except Exception:
        log.exception("feedparser failed for %s", source)
        return []

    items: list[Headline] = []
    for entry in parsed.entries[:max_items]:
        title = (getattr(entry, "title", "") or "").strip()
        link = (getattr(entry, "link", "") or "").strip()
        if not title:
            continue
        items.append(Headline(source=source, title=title, link=link))
    return items


def _fetch_feeds(feeds: Iterable[tuple[str, str]], max_per_feed: int = MAX_PER_FEED) -> list[Headline]:
    out: list[Headline] = []
    seen_titles: set[str] = set()
    for source, url in feeds:
        for h in _fetch_feed(url, source, max_per_feed):
            key = h.title.lower()[:80]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            out.append(h)
    return out


# ---------- Weather ----------

def fetch_weather(lat: float, lon: float, tz: str) -> str:
    """Return a one-line forecast string. Empty string on error."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,precipitation_probability_max,wind_speed_10m_max",
        "timezone": tz,
        "forecast_days": 1,
    }
    try:
        with httpx.Client(timeout=PER_FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        log.exception("weather fetch failed")
        return ""

    try:
        cur = data.get("current") or {}
        daily = data.get("daily") or {}
        cur_temp  = cur.get("temperature_2m")
        cur_feels = cur.get("apparent_temperature")
        cur_code  = cur.get("weather_code")
        cur_wind  = cur.get("wind_speed_10m")
        d_max     = (daily.get("temperature_2m_max") or [None])[0]
        d_min     = (daily.get("temperature_2m_min") or [None])[0]
        d_max_fl  = (daily.get("apparent_temperature_max") or [None])[0]
        d_min_fl  = (daily.get("apparent_temperature_min") or [None])[0]
        d_code    = (daily.get("weather_code") or [cur_code])[0]
        d_pop     = (daily.get("precipitation_probability_max") or [None])[0]
        d_wind    = (daily.get("wind_speed_10m_max") or [None])[0]

        cur_desc = WMO_DESCRIPTIONS.get(cur_code, "")
        day_desc = WMO_DESCRIPTIONS.get(d_code, cur_desc)

        bits: list[str] = []
        if cur_temp is not None:
            tline = f"{cur_temp:.0f}°C now"
            if cur_feels is not None and abs(cur_feels - cur_temp) >= 1:
                tline += f" (feels {cur_feels:.0f}°C)"
            bits.append(tline)
        if cur_desc:
            bits.append(cur_desc)
        if d_max is not None and d_min is not None:
            hline = f"high {d_max:.0f}°C / low {d_min:.0f}°C"
            if d_max_fl is not None and d_min_fl is not None:
                hline += f" (feels {d_max_fl:.0f}°/{d_min_fl:.0f}°)"
            bits.append(hline)
        if day_desc and day_desc != cur_desc:
            bits.append(day_desc + " expected")
        if d_pop is not None and d_pop >= 30:
            bits.append(f"{int(d_pop)}% chance of precip")
        if cur_wind is not None:
            bits.append(f"wind {cur_wind:.0f} km/h")
        elif d_wind is not None:
            bits.append(f"wind up to {d_wind:.0f} km/h")
        return ", ".join(bits)
    except Exception:
        log.exception("weather parse failed")
        return ""


# ---------- Sun times ----------

def fetch_sun_times(lat: float, lon: float, today: date, tz: str) -> tuple[str, str]:
    """Return (sunrise, sunset) as 'HH:MM' strings. Empty strings on error."""
    try:
        from astral import LocationInfo
        from astral.sun import sun
        loc = LocationInfo("local", "earth", tz, lat, lon)
        s = sun(loc.observer, date=today, tzinfo=ZoneInfo(tz))
        return (
            s["sunrise"].strftime("%H:%M"),
            s["sunset"].strftime("%H:%M"),
        )
    except Exception:
        log.exception("sun-times calculation failed")
        return ("", "")


# ---------- Wikipedia on-this-day ----------

def fetch_notable(today: date, max_items: int = MAX_NOTABLE) -> list[str]:
    """Wikipedia 'on this day' for today: holidays + selected events."""
    url = WIKI_ONTHISDAY_URL.format(mm=today.month, dd=today.day)
    try:
        with httpx.Client(timeout=PER_FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        log.exception("on-this-day fetch failed")
        return []

    out: list[str] = []
    # Holidays first — they're always relevant for "today".
    for h in (data.get("holidays") or [])[:2]:
        text = (h.get("text") or "").strip()
        if text:
            out.append(text)

    # Selected anniversaries: prefer the curated list ("selected") over raw "events".
    pool = data.get("selected") or data.get("events") or []
    for ev in pool:
        if len(out) >= max_items:
            break
        year = ev.get("year")
        text = (ev.get("text") or "").strip()
        if not text:
            continue
        if year is not None:
            out.append(f"{year}: {text}")
        else:
            out.append(text)
    return out[:max_items]


# ---------- Orchestrator ----------

def build_context(
    *,
    today: date,
    location_name: str,
    location_lat: float,
    location_lon: float,
    timezone: str,
) -> BriefContext:
    """Fetch every source and assemble a BriefContext. Failures degrade gracefully."""
    weather_line = fetch_weather(location_lat, location_lon, timezone)
    sunrise, sunset = fetch_sun_times(location_lat, location_lon, today, timezone)

    world = _fetch_feeds(WORLD_FEEDS, max_per_feed=4)
    canada = _fetch_feeds(CANADA_FEEDS, max_per_feed=4)
    tech_pool = _fetch_feeds(TECH_FEEDS, max_per_feed=10)
    google_blog = _fetch_feed(GOOGLE_BLOG[1], GOOGLE_BLOG[0], max_items=5)

    google_filtered = [h for h in tech_pool if GOOGLE_KEYWORDS.search(h.title)]
    ai_tech = [h for h in tech_pool if not GOOGLE_KEYWORDS.search(h.title)][:8]
    google = (google_blog + google_filtered)[:8]

    notable = fetch_notable(today, max_items=MAX_NOTABLE)

    return BriefContext(
        location_name=location_name,
        weather_line=weather_line,
        sunrise=sunrise,
        sunset=sunset,
        world=world[:8],
        canada=canada[:8],
        ai_tech=ai_tech,
        google=google,
        notable=notable,
    )


# ---------- Prompt formatting ----------

def _format_section(label: str, items: list[Headline]) -> str:
    if not items:
        return f"{label}: (no items fetched)\n"
    lines = [f"{label}:"]
    for h in items:
        lines.append(f"  - [{h.source}] {h.title}")
    return "\n".join(lines) + "\n"


def format_for_prompt(ctx: BriefContext) -> str:
    """Render the BriefContext as a plaintext block for inclusion in the prompt.

    The Copilot prompt then asks the model to summarize / rewrite this raw
    material in the desired tone and structure. Empty sections are still
    included so the model knows that source returned nothing.
    """
    parts: list[str] = ["RAW SOURCE DATA (for you to summarize):\n"]

    env_bits = []
    if ctx.weather_line:
        env_bits.append(f"Weather ({ctx.location_name}): {ctx.weather_line}")
    if ctx.sunrise and ctx.sunset:
        env_bits.append(f"Sunrise {ctx.sunrise} · Sunset {ctx.sunset}")
    if env_bits:
        parts.append("\n".join(env_bits) + "\n")

    parts.append(_format_section("World headlines", ctx.world))
    parts.append(_format_section("Canada headlines", ctx.canada))
    parts.append(_format_section("AI / Tech headlines", ctx.ai_tech))
    parts.append(_format_section("Google-related headlines", ctx.google))

    if ctx.notable:
        parts.append("Notable today (events / holidays / on-this-day):")
        for n in ctx.notable:
            parts.append(f"  - {n}")
        parts.append("")
    else:
        parts.append("Notable today: (none fetched)\n")

    return "\n".join(parts).rstrip() + "\n"
