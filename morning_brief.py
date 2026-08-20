#!/usr/bin/env python3
"""Daily investment brief -> LINE Official Account broadcast."""

import os
import sys
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
NEWS_TOPICS = [
    ("AI & Tech", "artificial intelligence OR Nvidia OR AI chip stocks"),
    ("Fed & Treasuries", "Federal Reserve OR Treasury yields OR bond market"),
    ("Markets & Macro", "stock market OR S&P 500 OR earnings OR economy"),
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


def get_news(per=3):
    out = {}
    for label, q in NEWS_TOPICS:
        url = (
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(q)
            + "&hl=en-US&gl=US&ceid=US:en"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            out[label] = [e.title for e in parsed.entries[:per]]
        except Exception as e:
            print(f"news: failed {label}: {e}", file=sys.stderr)
            out[label] = []
    return out


def news_text(news):
    parts = []
    for label, titles in news.items():
        if not titles:
            continue
        parts.append(label + ":")
        parts.extend(f"- {t}" for t in titles)
        parts.append("")
    return "\n".join(parts).strip() or "(news unavailable)"


def build_data(equities, commod, rates, news):
    blocks = [
        "EQUITIES:\n" + (equities or "(unavailable)"),
        "US TREASURY YIELDS:\n" + (rates or "(unavailable)"),
        "COMMODITIES / FX / CRYPTO:\n" + (commod or "(unavailable)"),
        "NEWS BY THEME:\n" + news_text(news),
    ]
    return "\n\n".join(blocks)


def write_brief_with_claude(data, date_str):
    prompt = f"""You are writing a concise daily INVESTMENT brief for a markets-focused reader. It will be sent as a LINE text message. Keep it under 1800 characters. Plain text only - no markdown, no asterisks, no '#'. Simple emoji and line breaks are fine.

Structure:
- Opening line with an emoji and the date.
- 1-2 sentences on the overall market tone, referencing rates/AI where relevant.
- "Markets" section: equities, then Treasury yields, then commodities/FX/crypto (keep the numbers exactly as given).
- "What's driving it" section: 4-6 of the most investment-relevant headlines below, each rewritten to one tight line, favoring AI, Fed/Treasuries, and major market moves.
- One-line sign-off.

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
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Claude API {r.status_code}: {r.text}")
    data = r.json()
    text = "".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    ).strip()
    if not text:
        raise RuntimeError("Claude returned empty text")
    return text


def format_brief_plain(equities, commod, rates, news, date_str):
    lines = [f"📈 Investment Brief — {date_str}", ""]
    lines += ["📊 Equities", equities or "(unavailable)", ""]
    lines += ["🏦 US Treasury Yields", rates or "(unavailable)", ""]
    lines += ["🪙 Commodities / FX / Crypto", commod or "(unavailable)", ""]
    lines += ["📰 What's driving it", news_text(news), ""]
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
    news = get_news()

    brief = None
    if ANTHROPIC_API_KEY:
        try:
            brief = write_brief_with_claude(
                build_data(equities, commod, rates, news), date_str
            )
            print("Brief written by Claude.")
        except Exception as e:
            print(f"Claude unavailable, using self-formatted brief: {e}", file=sys.stderr)
    if not brief:
        brief = format_brief_plain(equities, commod, rates, news, date_str)
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
