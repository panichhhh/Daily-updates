#!/usr/bin/env python3
"""Daily investment brief -> LINE Official Account broadcast."""

import os
import re
import sys
import html
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
    ("SET (Thailand)", "^SET.BK", "{:,.2f}"),
]
COMMODITIES_FX = [
    ("Gold", "GC=F", "${:,.0f}"),
    ("WTI Oil", "CL=F", "${:,.2f}"),
    ("Dollar (DXY)", "DX-Y.NYB", "{:,.2f}"),
    ("USD/THB", "THB=X", "{:,.3f}"),
    ("BTC", "BTC-USD", "${:,.0f}"),
    ("ETH", "ETH-USD", "${:,.0f}"),
]
RATES = [
    ("US 3M", "^IRX"),
    ("US 5Y", "^FVX"),
    ("US 10Y", "^TNX"),
    ("US 30Y", "^TYX"),
]

SOURCE_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "https://finance.yahoo.com/news/rssindex",
    "https://www.investing.com/rss/news_25.rss",
]
SUPPLEMENT_SEARCHES = [
    "US Treasury buyback OR quarterly refunding OR debt management",
    "Federal Reserve OR Treasury yields OR interest rates",
    "artificial intelligence OR Nvidia OR semiconductor stocks",
    "inflation OR jobs report OR GDP OR economy",
    "stock market OR S&P 500 OR earnings",
]
KEYWORDS = [
    ("treasury", 3), ("buyback", 3), ("refunding", 3), ("debt", 2),
    ("fed", 3), ("federal reserve", 3), ("yield", 3), ("rate cut", 3),
    ("rates", 2), ("inflation", 3), ("cpi", 3), ("ppi", 2), ("jobs", 2),
    ("payroll", 2), ("gdp", 2), ("tariff", 2), ("bond", 2),
    ("nvidia", 3), ("ai", 3), ("artificial intelligence", 3),
    ("semiconductor", 3), ("chip", 2), ("earnings", 1), ("s&p", 1),
    ("nasdaq", 1), ("dollar", 1), ("oil", 1), ("gold", 1),
]


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


if not LINE_TOKEN and not DRY_RUN:
    die("LINE_CHANNEL_ACCESS_TOKEN is not set")


def yahoo_quote(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = requests.get(
        url, headers=HEADERS, params={"range": "5d", "interval": "1d"}, timeout=15
    )
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
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


def clean_desc(raw, title):
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
    if len(txt) > 260:
        txt = txt[:260].rsplit(" ", 1)[0] + "…"
    return txt


def fetch_entries(url):
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    feed_title = ""
    if getattr(parsed, "feed", None):
        feed_title = parsed.feed.get("title", "") or ""
    items = []
    for e in parsed.entries:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        src = ""
        s = e.get("source")
        if s and getattr(s, "get", None):
            src = s.get("title", "") or ""
        if not src:
            src = feed_title
        items.append(
            {
                "title": title,
                "summary": clean_desc(e.get("summary", ""), title),
                "source": src,
            }
        )
    return items


def gnews_url(query):
    return (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query + " when:2d")
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def collect_news():
    seen, items = set(), []

    def add(entries, cap=None):
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

    for url in SOURCE_FEEDS:
        try:
            add(fetch_entries(url))
        except Exception as e:
            print(f"news: failed feed {url}: {e}", file=sys.stderr)
    for q in SUPPLEMENT_SEARCHES:
        try:
            add(fetch_entries(gnews_url(q)), cap=5)
        except Exception as e:
            print(f"news: failed search '{q}': {e}", file=sys.stderr)
    return items


def score_item(it):
    t = (it["title"] + " " + it.get("summary", "")).lower()
    return sum(w for kw, w in KEYWORDS if kw in t)


def rank_news(items, n=8):
    ranked = sorted(items, key=score_item, reverse=True)
    top = [it for it in ranked if score_item(it) > 0][:n]
    if len(top) < n:
        for it in ranked:
            if it not in top:
                top.append(it)
            if len(top) >= n:
                break
    return top


def news_text(items):
    if not items:
        return "(news unavailable)"
    out = []
    for it in items:
        line = "• " + it["title"]
        if it.get("source"):
            line += f" ({it['source']})"
        if it.get("summary"):
            line += "\n  " + it["summary"]
        out.append(line)
    return "\n".join(out)


def build_data(equities, commod, rates, items):
    blocks = [
        "EQUITIES:\n" + (equities or "(unavailable)"),
        "US TREASURY YIELDS:\n" + (rates or "(unavailable)"),
        "COMMODITIES / FX / CRYPTO:\n" + (commod or "(unavailable)"),
        "NEWS ITEMS (title, source, summary) - ranked most relevant first:\n"
        + news_text(items),
    ]
    return "\n\n".join(blocks)


def write_brief_with_claude(data, date_str):
    prompt = f"""You are a markets strategist writing a concise daily INVESTMENT brief. It is sent as a LINE text message, so use plain text only - no markdown, no asterisks, no '#'. Simple emoji and line breaks are fine. Target 1800-2600 characters.

Sections:
1) Opening line with an emoji and the date.
2) "Markets" - list equities, then Treasury yields (with bp moves), then commodities/FX/crypto. Keep every number exactly as given.
3) "News" - summarize the 6-8 most investment-relevant items below. Write each as a single tight sentence (two if a number matters), leading with what happened and the key figure. Prioritize the Fed/Treasury/rates, AI/semis, inflation/jobs data, and major market moves. Do NOT invent figures not present in the data; if a summary is missing, summarize from the headline only.
4) One-line sign-off.

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
            "max_tokens": 1800,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
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


def format_brief_plain(equities, commod, rates, items, date_str):
    lines = [f"📈 Investment Brief — {date_str}", ""]
    lines += ["📊 Equities", equities or "(unavailable)", ""]
    lines += ["🏦 US Treasury Yields", rates or "(unavailable)", ""]
    lines += ["🪙 Commodities / FX / Crypto", commod or "(unavailable)", ""]
    lines += ["📰 Top investment news", news_text(items), ""]
    lines += ["Have a sharp day in the markets."]
    return "\n".join(lines)


def broadcast(text):
    text = text[:4900]
    r = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"messages": [{"type": "text", "text": text}]},
        timeout=30,
    )
    if r.status_code != 200:
        die(f"LINE API error {r.status_code}: {r.text}")
    print("Broadcast sent OK")


def main():
    now = datetime.datetime.now(zoneinfo.ZoneInfo(TZ))
    date_str = now.strftime("%A, %d %B %Y")

    equities = quote_block(EQUITIES)
    commod = quote_block(COMMODITIES_FX)
    rates = rates_block()
    items = rank_news(collect_news())

    brief = None
    if ANTHROPIC_API_KEY:
        try:
            brief = write_brief_with_claude(
                build_data(equities, commod, rates, items), date_str
            )
            print("Brief written by Claude.")
        except Exception as e:
            print(f"Claude unavailable, using self-formatted brief: {e}", file=sys.stderr)
    if not brief:
        brief = format_brief_plain(equities, commod, rates, items, date_str)
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
