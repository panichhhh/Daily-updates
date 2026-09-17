#!/usr/bin/env python3
"""Daily investment brief (English, comprehensive) -> LINE Official Account."""

import os
import re
import sys
import html
import time
import datetime
import zoneinfo
import urllib.parse

import requests
import feedparser

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
TZ = os.environ.get("BRIEF_TIMEZONE", "Asia/Bangkok")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA}

EQUITIES = [
    ("S&P 500", "^GSPC", "{:,.2f}"),
    ("Nasdaq", "^IXIC", "{:,.2f}"),
    ("Dow", "^DJI", "{:,.2f}"),
    ("Russell 2000", "^RUT", "{:,.2f}"),
    ("VIX", "^VIX", "{:,.2f}"),
]
COMMODITIES_FX = [
    ("Gold", "GC=F", "${:,.0f}"),
    ("Silver", "SI=F", "${:,.2f}"),
    ("Dollar (DXY)", "DX-Y.NYB", "{:,.2f}"),
    ("USD/JPY", "JPY=X", "{:,.2f}"),
    ("EUR/USD", "EURUSD=X", "{:,.4f}"),
    ("BTC", "BTC-USD", "${:,.0f}"),
    ("ETH", "ETH-USD", "${:,.0f}"),
]
ENERGY = [
    ("Brent", "BZ=F", "${:,.2f}"),
    ("WTI", "CL=F", "${:,.2f}"),
    ("RBOB Gasoline", "RB=F", "${:,.3f}"),
    ("Heating Oil/Diesel", "HO=F", "${:,.3f}"),
    ("Nat Gas", "NG=F", "${:,.3f}"),
]
RATES = [
    ("US 3M", "^IRX"),
    ("US 5Y", "^FVX"),
    ("US 10Y", "^TNX"),
    ("US 30Y", "^TYX"),
]
THAI_MARKET = [
    ("SET Index", "^SET.BK", "{:,.2f}"),
    ("USD/THB", "THB=X", "{:,.3f}"),
]
MOVERS_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "AVGO", "AMD", "JPM", "XOM", "LLY", "NFLX", "CRM",
]

SOURCE_FEEDS = [
    "https://www.investing.com/rss/news_25.rss",
    "https://www.investing.com/rss/news_1.rss",
    "https://www.investing.com/rss/news_11.rss",
    "https://www.investing.com/rss/news_14.rss",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "https://finance.yahoo.com/news/rssindex",
]
SUPPLEMENT_SEARCHES = [
    "Federal Reserve OR interest rate outlook OR rate cut",
    "US inflation OR CPI OR PCE OR labor market OR jobs report",
    "US Treasury yields OR bond market OR 10-year yield",
    "central bank OR ECB OR BOJ OR geopolitics OR tariffs OR China economy",
    "S&P 500 OR Nasdaq OR big tech OR Nvidia OR semiconductor stocks",
]
ENERGY_SEARCHES = [
    "oil price outlook OR crude oil forecast OR OPEC production",
    "refining margin OR crack spread OR Singapore GRM OR refinery",
    "petrochemical prices OR ethylene OR polyethylene OR paraxylene OR naphtha",
]
THAI_FEEDS = [
    "https://www.kaohoon.com/feed",
    "https://www.kaohoon.com/category/latest-news/feed",
    "https://www.bangkokpost.com/rss/data/business.xml",
    "https://www.prachachat.net/feed",
]
THAI_SEARCHES = [
    "SET Index OR Thailand stock market OR Thai stocks",
    "Bank of Thailand OR Thai baht OR SET50 OR Thailand economy",
    "site:efinancethai.com",
    "Stock Exchange of Thailand OR site:set.or.th",
]
KEYWORDS = [
    ("central bank", 3), ("federal reserve", 3), ("fed", 3), ("ecb", 3),
    ("boj", 3), ("rate cut", 3), ("rate hike", 3), ("rates", 2),
    ("interest rate", 3), ("inflation", 3), ("cpi", 3), ("pce", 3), ("ppi", 2),
    ("jobs", 2), ("payroll", 2), ("labor", 2), ("unemployment", 2), ("gdp", 2),
    ("treasury", 3), ("yield", 3), ("bond", 2), ("buyback", 3), ("debt", 2),
    ("oil", 2), ("opec", 3), ("brent", 2), ("crude", 2), ("gold", 2),
    ("tariff", 2), ("geopolit", 2), ("china", 2), ("currency", 2),
    ("dollar", 2), ("nvidia", 2), ("artificial intelligence", 2), (" ai ", 2),
    ("semiconductor", 2), ("earnings", 1), ("s&p", 1), ("nasdaq", 1),
]


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


if not LINE_TOKEN and not DRY_RUN:
    die("LINE_CHANNEL_ACCESS_TOKEN is not set")


def yahoo_quote(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = requests.get(
        url, headers=HEADERS, params={"range": "1mo", "interval": "1d"}, timeout=15
    )
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    meta = result.get("meta", {})
    closes = []
    try:
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
    except Exception:
        pass
    if len(closes) >= 2:
        return closes[-1], closes[-2]
    price = meta.get("regularMarketPrice")
    if price is None and closes:
        price = closes[-1]
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    return price, prev


def quote_block(rows):
    lines = []
    for label, sym, fmt in rows:
        try:
            price, prev = yahoo_quote(sym)
            if price is None or prev is None:
                continue
            chg = (price - prev) / prev * 100 if prev else 0.0
            arrow = "▲" if chg >= 0 else "▼"
            lines.append(f"{label}: {fmt.format(price)} {arrow}{abs(chg):.2f}%")
        except Exception as e:
            print(f"markets: failed {label}: {e}", file=sys.stderr)
    return "\n".join(lines)


def rates_block():
    lines = []
    for label, sym in RATES:
        try:
            price, prev = yahoo_quote(sym)
            if price is None or prev is None:
                continue
            y = price / 10 if price > 20 else price
            yp = prev / 10 if prev > 20 else prev
            bp = (y - yp) * 100
            arrow = "▲" if bp >= 0 else "▼"
            lines.append(f"{label}: {y:.2f}% {arrow}{abs(bp):.0f}bp")
        except Exception as e:
            print(f"rates: failed {label}: {e}", file=sys.stderr)
    return "\n".join(lines)


def get_movers(n=5):
    data = []
    for t in MOVERS_UNIVERSE:
        try:
            price, prev = yahoo_quote(t)
            if price and prev:
                chg = (price - prev) / prev * 100
                data.append((t, price, chg))
        except Exception as e:
            print(f"movers: failed {t}: {e}", file=sys.stderr)
    data.sort(key=lambda x: abs(x[2]), reverse=True)
    return data[:n]


def movers_text(movers):
    if not movers:
        return "(unavailable)"
    out = []
    for t, p, c in movers:
        arrow = "▲" if c >= 0 else "▼"
        out.append(f"{t}: ${p:,.2f} {arrow}{abs(c):.2f}%")
    return "\n".join(out)


def clean_text(raw, title, max_len=300):
    if not raw:
        return ""
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    low = txt.lower()
    if not txt or "view full coverage" in low or "continue reading" in low:
        return ""
    if title and txt[:50].lower() == title[:50].lower():
        return ""
    if len(txt) > max_len:
        txt = txt[:max_len].rsplit(" ", 1)[0] + "…"
    return txt


def fetch_entries(url, max_age_hours=24, max_chars=300, prefer_content=False):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    feed_title = ""
    if getattr(parsed, "feed", None):
        feed_title = parsed.feed.get("title", "") or ""
    now = time.time()
    items = []
    for e in parsed.entries:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        tp = e.get("published_parsed") or e.get("updated_parsed")
        if tp is not None:
            age_h = (now - time.mktime(tp)) / 3600.0
            if age_h > max_age_hours:
                continue
        raw = ""
        if prefer_content:
            c = e.get("content")
            if c and isinstance(c, list) and c and c[0].get("value"):
                raw = c[0]["value"]
        if not raw:
            raw = e.get("summary", "")
        src = ""
        s = e.get("source")
        if s and getattr(s, "get", None):
            src = s.get("title", "") or ""
        if not src:
            src = feed_title
        items.append(
            {
                "title": title,
                "summary": clean_text(raw, title, max_chars),
                "source": src,
            }
        )
    return items


def gnews_url(query, region="US"):
    tail = "&hl=en-TH&gl=TH&ceid=TH:en" if region == "TH" else "&hl=en-US&gl=US&ceid=US:en"
    return (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query + " when:1d")
        + tail
    )


def _add(seen, items, entries, cap=None):
    n = 0
    for it in entries:
        key = it["title"].lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        items.append(it)
        n += 1
        if cap and n >= cap:
            break


def collect_news():
    seen, items = set(), []
    for url in SOURCE_FEEDS:
        try:
            _add(seen, items, fetch_entries(url))
        except Exception as e:
            print(f"news: failed feed {url}: {e}", file=sys.stderr)
    for q in SUPPLEMENT_SEARCHES:
        try:
            _add(seen, items, fetch_entries(gnews_url(q)), cap=6)
        except Exception as e:
            print(f"news: failed search '{q}': {e}", file=sys.stderr)
    return items


def collect_energy_news(cap_total=6):
    seen, items = set(), []
    for q in ENERGY_SEARCHES:
        try:
            _add(seen, items, fetch_entries(gnews_url(q)), cap=4)
        except Exception as e:
            print(f"news: failed energy '{q}': {e}", file=sys.stderr)
    return items[:cap_total]


def collect_thai_news(cap_total=9):
    seen, items = set(), []
    for url in THAI_FEEDS:
        try:
            _add(seen, items, fetch_entries(url, max_chars=500, prefer_content=True),
                 cap=4)
        except Exception as e:
            print(f"news: failed thai feed {url}: {e}", file=sys.stderr)
    for q in THAI_SEARCHES:
        try:
            _add(seen, items, fetch_entries(gnews_url(q, region="TH")), cap=3)
        except Exception as e:
            print(f"news: failed thai search '{q}': {e}", file=sys.stderr)
    return items[:cap_total]


def is_macro(it):
    t = (it["title"] + " " + it.get("summary", "")).lower()
    macro = ("central bank", "federal reserve", "fed", "ecb", "boj", "rate",
             "inflation", "cpi", "pce", "gdp", "labor", "jobs", "treasury",
             "yield", "bond", "oil", "opec", "brent", "gold", "tariff",
             "geopolit", "china", "currency", "dollar")
    return any(k in t for k in macro)


def score_item(it):
    t = (" " + it["title"] + " " + it.get("summary", "") + " ").lower()
    return sum(w for kw, w in KEYWORDS if kw in t)


def rank_news(items, n=10):
    ranked = sorted(items, key=score_item, reverse=True)
    macro = [it for it in ranked if is_macro(it)]
    need_macro = max(n // 2, 1)
    out = macro[:need_macro]
    for it in ranked:
        if it in out:
            continue
        out.append(it)
        if len(out) >= n:
            break
    return out[:n]


def news_text(items):
    if not items:
        return "(none found in last 24h)"
    out = []
    for it in items:
        line = "• " + it["title"]
        if it.get("source"):
            line += f" ({it['source']})"
        if it.get("summary"):
            line += "\n  " + it["summary"]
        out.append(line)
    return "\n".join(out)


def build_data(equities, commod, energy, rates, movers, world, energy_news,
               thai_mkt, thai_news):
    blocks = [
        "EQUITIES:\n" + (equities or "(unavailable)"),
        "US TREASURY YIELDS (level and daily bp change):\n" + (rates or "(unavailable)"),
        "COMMODITIES / FX / CRYPTO:\n" + (commod or "(unavailable)"),
        "ENERGY COMPLEX (crude, products, gas):\n" + (energy or "(unavailable)"),
        "LARGE-CAP MOVERS (biggest % moves today):\n" + movers_text(movers),
        "WORLD NEWS (last 24h, ranked, macro first):\n" + news_text(world),
        "ENERGY / REFINING / PETROCHEMICAL NEWS:\n" + news_text(energy_news),
        "THAI MARKET DATA:\n" + (thai_mkt or "(unavailable)"),
        "THAI NEWS (Kaohoon / Bangkok Post / Prachachat / efinanceThai / SET):\n"
        + news_text(thai_news),
    ]
    return "\n\n".join(blocks)


def write_brief_with_claude(data, date_str):
    prompt = f"""You are a senior markets strategist and financial content writer. Write today's INVESTMENT brief in ENGLISH for informed general investors, using ONLY the data below. Do not use old or remembered news, and do not invent any figure not present in the data. Translate any Thai-language content into clear English.

Goal: COMPREHENSIVE and DETAILED enough that the reader gets the complete picture, but written efficiently - short paragraphs and crisp bullet lines, no padding. It is delivered over LINE and may span a few messages, so depth is fine (aim ~5000-7000 characters). Plain text only: no markdown, no asterisks, no '#'. Emoji as section markers and line breaks are fine.

Write these sections in order:

1) Header: an emoji + the date, then a 2-3 sentence executive summary of the single most important takeaway from overnight trading.
2) "Market drivers" - 3-5 sentences explaining WHAT moved markets and WHY, connecting equities, Treasury yields, the dollar, oil and the key news into one narrative.
3) "Markets" - list equities, then Treasury yields (with bp moves), then commodities/FX/crypto. Keep every number exactly as given, and add a few words of context on the notable moves.
4) "Stocks in focus" - the 5 large-cap movers below; for each: the move plus 1-2 sentences on the reason (from the news where possible) and what it signals.
5) "Economic & Fed watch" - detailed but concise: the Fed stance, US rate-cut outlook, inflation, the labor market, and Treasury yields, citing the levels/changes. Then clearly explain how these affect growth stocks, tech stocks, gold, and the US dollar (2-4 sentences).
6) "Energy & petrochemicals" - crude price and outlook, refining margins / crack spreads, and petrochemical (ethylene, PX, PE, naphtha) trends from the data/news, with the read-through for Thai energy/refiner/petrochem names (PTT, PTTEP, TOP, SPRC, IRPC, PTTGC, IVL) where supported.
7) "Global & macro news" - 4-6 other important items, each 1-2 sentences with the key figures.
8) "Thai market" - the SET Index and USD/THB figures with brief context, then summarize 4-6 of the Thai news items below (from Kaohoon, Bangkok Post, Prachachat, etc.) in 1-2 detailed sentences each, naming the stocks/sectors involved.
9) "Watch today/tonight" - a concise bullet list of the key events, data releases, earnings and price levels investors should watch next.
10) One-line sign-off.

DATE: {date_str}

DATA:
{data}
"""
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=240,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Claude API {r.status_code}: {r.text}")
    body = r.json()
    text = "".join(
        b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"
    ).strip()
    if not text:
        raise RuntimeError("Claude returned empty text")
    return text


def format_brief_plain(equities, commod, energy, rates, movers, world,
                       energy_news, thai_mkt, thai_news, date_str):
    lines = [f"📈 Investment Brief — {date_str}", ""]
    lines += ["📊 Equities", equities or "(unavailable)", ""]
    lines += ["🏦 US Treasury Yields", rates or "(unavailable)", ""]
    lines += ["🪙 Commodities / FX / Crypto", commod or "(unavailable)", ""]
    lines += ["⛽ Energy complex", energy or "(unavailable)", ""]
    lines += ["🔥 Large-cap movers", movers_text(movers), ""]
    lines += ["🌏 Global news (last 24h)", news_text(world), ""]
    lines += ["🛢️ Energy / refining / petrochemical news", news_text(energy_news), ""]
    lines += ["🇹🇭 Thai market", thai_mkt or "(unavailable)", ""]
    lines += ["🇹🇭 Thai news", news_text(thai_news), ""]
    lines += ["(Enable the Claude API key for the written analysis sections.)"]
    return "\n".join(lines)


def split_message(text, limit=4800, max_parts=5):
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n\n"):
        block = para + "\n\n"
        if len(block) > limit:
            for line in block.split("\n"):
                if len(cur) + len(line) + 1 > limit and cur:
                    parts.append(cur.rstrip())
                    cur = ""
                cur += line + "\n"
            continue
        if len(cur) + len(block) > limit and cur:
            parts.append(cur.rstrip())
            cur = ""
        cur += block
    if cur.strip():
        parts.append(cur.rstrip())
    return parts[:max_parts]


def broadcast(text):
    parts = split_message(text)
    messages = [{"type": "text", "text": p[:4900]} for p in parts]
    r = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"messages": messages},
        timeout=30,
    )
    if r.status_code != 200:
        die(f"LINE API error {r.status_code}: {r.text}")
    print(f"Broadcast sent OK ({len(messages)} message(s))")


def main():
    now = datetime.datetime.now(zoneinfo.ZoneInfo(TZ))
    date_str = now.strftime("%A, %d %B %Y")

    equities = quote_block(EQUITIES)
    commod = quote_block(COMMODITIES_FX)
    energy = quote_block(ENERGY)
    rates = rates_block()
    thai_mkt = quote_block(THAI_MARKET)
    movers = get_movers()
    world = rank_news(collect_news())
    energy_news = collect_energy_news()
    thai_news = collect_thai_news()

    brief = None
    if ANTHROPIC_API_KEY:
        try:
            brief = write_brief_with_claude(
                build_data(equities, commod, energy, rates, movers, world,
                           energy_news, thai_mkt, thai_news),
                date_str,
            )
            print("Brief written by Claude.")
        except Exception as e:
            print(f"Claude unavailable, using self-formatted brief: {e}", file=sys.stderr)
    if not brief:
        brief = format_brief_plain(
            equities, commod, energy, rates, movers, world, energy_news,
            thai_mkt, thai_news, date_str
        )
        print("Brief self-formatted (no Claude).")

    print("----- BRIEF -----")
    print(brief)
    print("-----------------")

    if DRY_RUN:
        print("DRY_RUN=1 -> not sending to LINE")
        return
    broadcast(brief)


if __name__ == "__main__":
    main()
