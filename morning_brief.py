#!/usr/bin/env python3
"""Daily morning brief -> LINE Official Account broadcast."""

import os
import sys
import datetime
import zoneinfo

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


def get_markets():
    symbols = [
        ("S&P 500", "^GSPC", "{:,.2f}"),
        ("Nasdaq", "^IXIC", "{:,.2f}"),
        ("Dow", "^DJI", "{:,.2f}"),
        ("SET (Thailand)", "^SET.BK", "{:,.2f}"),
        ("USD/THB", "THB=X", "{:,.3f}"),
        ("BTC", "BTC-USD", "${:,.0f}"),
        ("ETH", "ETH-USD", "${:,.0f}"),
    ]
    lines = []
    for label, sym, fmt in symbols:
        try:
            price, prev = yahoo_quote(sym)
            if price is None or prev is None:
                continue
            chg = (price - prev) / prev * 100 if prev else 0.0
            arrow = "▲" if chg >= 0 else "▼"
            lines.append(f"{label}: {fmt.format(price)} {arrow}{abs(chg):.2f}%")
        except Exception as e:
            print(f"markets: failed {label}: {e}", file=sys.stderr)
    return "\n".join(lines) if lines else "(markets data unavailable)"


def get_news(n=8):
    feeds = [
        "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss?hl=en-TH&gl=TH&ceid=TH:en",
    ]
    headlines = []
    for f in feeds:
        try:
            resp = requests.get(f, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:n]:
                headlines.append(entry.title)
        except Exception as e:
            print(f"news: failed {f}: {e}", file=sys.stderr)
    seen, out = set(), []
    for h in headlines:
        if h not in seen:
            seen.add(h)
            out.append(h)
        if len(out) >= n:
            break
    return "\n".join(f"- {h}" for h in out) if out else "(news unavailable)"


def write_brief_with_claude(markets, news, date_str):
    prompt = f"""You are writing a short daily morning brief that will be sent as a LINE text message. Keep it under 1500 characters. Plain text only - no markdown, no asterisks, no '#' headers. Simple emoji and line breaks are fine. Be crisp, warm, and useful.

Structure it like this:
- An opening line with a sun emoji and the date.
- One sentence reading the market mood.
- A "Markets" section listing the figures below (keep the numbers exactly as given).
- A "Headlines" section with the 4-6 most important items below, each rewritten to one tight line.
- A short one-line sign-off.

DATE: {date_str}

MARKET DATA:
{markets}

NEWS HEADLINES:
{news}
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
            "max_tokens": 1024,
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


def format_brief_plain(markets, news, date_str):
    return "\n".join(
        [
            f"☀️ Morning Brief — {date_str}",
            "",
            "📊 Markets",
            markets,
            "",
            "📰 Headlines",
            news,
            "",
            "Have a great day!",
        ]
    )


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

    markets = get_markets()
    news = get_news()

    brief = None
    if ANTHROPIC_API_KEY:
        try:
            brief = write_brief_with_claude(markets, news, date_str)
            print("Brief written by Claude.")
        except Exception as e:
            print(f"Claude unavailable, using self-formatted brief: {e}", file=sys.stderr)
    if not brief:
        brief = format_brief_plain(markets, news, date_str)
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
