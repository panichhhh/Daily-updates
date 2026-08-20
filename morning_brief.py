#!/usr/bin/env python3
"""Daily investment brief (English) -> LINE Official Account broadcast."""

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
]
COMMODITIES_FX = [
    ("Gold", "GC=F", "${:,.0f}"),
    ("WTI Oil", "CL=F", "${:,.2f}"),
    ("Brent", "BZ=F", "${:,.2f}"),
    ("Dollar (DXY)", "DX-Y.NYB", "{:,.2f}"),
    ("BTC", "BTC-USD", "${:,.0f}"),
    ("ETH", "ETH-USD", "${:,.0f}"),
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
    "central bank OR Federal Reserve OR ECB OR BOJ OR interest rate decision",
    "inflation OR CPI OR jobs report OR GDP",
    "oil prices OR OPEC OR Brent crude OR gold",
    "US Treasury yields OR bond market OR buyback OR refunding",
    "geopolitics OR tariffs OR China economy OR currency",
    "artificial intelligence OR Nvidia OR semiconductor stocks",
]
THAI_SEARCHES = [
    "SET Index OR Thailand stock market OR Thai stocks",
    "Bank of Thailand OR Thai baht OR Thailand economy OR SET50",
]
KEYWORDS = [
    ("central bank", 3), ("federal reserve", 3), ("fed", 3), ("ecb", 3),
    ("boj", 3), ("boe", 2), ("rate cut", 3), ("rate hike", 3), ("rates", 2),
    ("interest rate", 3), ("inflation", 3), ("cpi", 3), ("ppi", 2),
    ("jobs", 2), ("payroll", 2), ("gdp", 2), ("treasury", 3), ("yield", 3),
    ("bond", 2), ("buyback", 3), ("refunding", 3), ("debt", 2),
    ("oil", 2), ("opec", 3), ("brent", 2), ("crude", 2), ("gold", 2),
    ("tariff", 2), ("geopolit", 2), ("china", 2), ("currency", 2),
    ("dollar", 2), ("yen", 1), ("euro", 1), ("nvidia", 2),
    ("artificial intelligence", 2), (" ai ", 2), ("semiconductor", 2),
    ("earnings", 1), ("s&p", 1), ("nasdaq", 1),
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
    if len(txt) > 300:
        txt = txt[:300].rsplit(" ", 1)[0] + "…"
    return txt


def fetch_entries(url, max_age_hours=24):
    resp = requests.get(url, headers=HEADERS, timeout=15)
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


def gnews_url(query, region="US"):
    if region == "TH":
        tail = "&hl=en-TH&gl=TH&ceid=TH:en"
    else:
        tail = "&hl=en-US&gl=US&ceid=US:en"
    return (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query + " when:1d")
        + tail
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
            add(fetch_entries(gnews_url(q)), cap=6)
        except Exception as e:
            print(f"news: failed search '{q}': {e}", file=sys.stderr)
    return items


def collect_thai_news(cap_total=5):
    seen, items = set(), []
    for q in THAI_SEARCHES:
        try:
            for it in fetch_entries(gnews_url(q, region="TH"))[:5]:
                key = it["title"].lower()[:60]
                if key in seen:
                    continue
                seen.add(key)
                items.append(it)
        except Exception as e:
            print(f"news: failed thai '{q}': {e}", file=sys.stderr)
    return items[:cap_total]


def is_macro(it):
    t = (it["title"] + " " + it.get("summary", "")).lower()
    macro = ("central bank", "federal reserve", "fed", "ecb", "boj", "rate",
             "inflation", "cpi", "gdp", "treasury", "yield", "bond", "oil",
             "opec", "brent", "gold", "tariff", "geopolit", "china",
             "currency", "dollar", "yen", "euro")
    return any(k in t for k in macro)


def score_item(it):
    t = (" " + it["title"] + " " + it.get("summary", "") + " ").lower()
    return sum(w for kw, w in KEYWORDS if kw in t)


def rank_news(items, n=8):
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


def build_data(equities, commod, rates, world, thai_mkt, thai_news):
    blocks = [
        "EQUITIES:\n" + (equities or "(unavailable)"),
        "US TREASURY YIELDS:\n" + (rates or "(unavailable)"),
        "COMMODITIES / FX / CRYPTO:\n" + (commod or "(unavailable)"),
        "WORLD NEWS (last 24h, ranked, macro first):\n" + news_text(world),
        "THAI MARKET DATA:\n" + (thai_mkt or "(unavailable)"),
        "THAI STOCK NEWS (last 24h):\n" + news_text(thai_news),
    ]
    return "\n\n".join(blocks)


def write_brief_with_claude(data, date_str):
    prompt = f"""You are a financial content writer who summarizes international financial news for general investors, with a deep understanding of markets, economics and investing. Write today's INVESTMENT brief in ENGLISH, using ONLY the data below (do not use old or remembered news; do not invent figures not present in the data).

It is sent as a LINE text message: plain text only, no markdown, no asterisks, no '#'. Emoji and line breaks are fine. Keep it under 2800 characters.

Sections:
1) Opening line with an emoji and the date.
2) "Markets" - equities, then US Treasury yields (with bp moves), then commodities/FX/crypto. Keep numbers exactly as given.
3) "Global news" - summarize the 6-8 world items below, one to two tight sentences each, at least half macro/global (central banks, rates, inflation, oil, indices, geopolitics, FX). Lead with what happened and the key figure.
4) "Thai market" - state the SET Index and USD/THB figures, then summarize the Thai-stock news items below (2-4 of them) in one to two sentences each.
5) One-line sign-off.

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
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
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


def format_brief_plain(equities, commod, rates, world, thai_mkt, thai_news, date_str):
    lines = [f"📈 Investment Brief — {date_str}", ""]
    lines += ["📊 Equities", equities or "(unavailable)", ""]
    lines += ["🏦 US Treasury Yields", rates or "(unavailable)", ""]
    lines += ["🪙 Commodities / FX / Crypto", commod or "(unavailable)", ""]
    lines += ["🌏 Global news (last 24h)", news_text(world), ""]
    lines += ["🇹🇭 Thai market", thai_mkt or "(unavailable)", ""]
    lines += ["🇹🇭 Thai stock news", news_text(thai_news), ""]
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
    thai_mkt = quote_block(THAI_MARKET)
    world = rank_news(collect_news())
    thai_news = collect_thai_news()

    brief = None
    if ANTHROPIC_API_KEY:
        try:
            brief = write_brief_with_claude(
                build_data(equities, commod, rates, world, thai_mkt, thai_news),
                date_str,
            )
            print("Brief written by Claude.")
        except Exception as e:
            print(f"Claude unavailable, using self-formatted brief: {e}", file=sys.stderr)
    if not brief:
        brief = format_brief_plain(
            equities, commod, rates, world, thai_mkt, thai_news, date_str
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
