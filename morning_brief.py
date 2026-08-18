#!/usr/bin/env python3
"""
Daily morning brief -> LINE Official Account broadcast.

What it does, every time it runs:
  1. Fetches a markets snapshot   (Stooq + CoinGecko, no API key needed)
  2. Fetches top news headlines    (Google News RSS, no API key needed)
  3. Asks Claude to write a short, LINE-friendly morning brief
  4. Broadcasts that text to everyone who follows your LINE OA

Required environment variables (set as GitHub Actions repo secrets):
  ANTHROPIC_API_KEY          - your Claude API key   (https://console.anthropic.com)
  LINE_CHANNEL_ACCESS_TOKEN  - long-lived token from the LINE Developers console

Optional environment variables:
  ANTHROPIC_MODEL   - model id (default below). See
                      https://docs.claude.com/en/docs/about-claude/models
  BRIEF_TIMEZONE    - timezone label for the date line (default 'Asia/Bangkok')
  DRY_RUN           - set to '1' to print the brief but NOT send to LINE
"""

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


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


if not ANTHROPIC_API_KEY:
    die("ANTHROPIC_API_KEY is not set")
if not LINE_TOKEN and not DRY_RUN:
    die("LINE_CHANNEL_ACCESS_TOKEN is not set")


def get_markets():
    """Index + FX + crypto quotes, all from free no-key endpoints."""
    lines = []

    # --- Stooq: indices & FX (CSV, no key) ---
    stooq = [
        ("S&P 500", "^spx"),
        ("Nasdaq", "^ndq"),
        ("Dow", "^dji"),
        ("SET (Thailand)", "^set"),
        ("USD/THB", "usdthb"),
    ]
    for label, sym in stooq:
        try:
            url = f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcvn&h&e=csv"
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            rows = r.text.strip().splitlines()
            if len(rows) < 2:
                continue
            # columns: Symbol,Date,Time,Open,High,Low,Close,Volume,Name
            cols = rows[1].split(",")
            openp, close = cols[3], cols[6]
            if openp in ("N/D", "") or close in ("N/D", ""):
                continue
            open_f, close_f = float(openp), float(close)
            chg = (close_f - open_f) / open_f * 100 if open_f else 0.0
            arrow = "▲" if chg >= 0 else "▼"  # ▲ / ▼
            lines.append(f"{label}: {close_f:,.2f} {arrow}{abs(chg):.2f}%")
        except Exception as e:
            print(f"markets: failed {label}: {e}", file=sys.stderr)

    # --- CoinGecko: crypto (no key) ---
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=15,
        )
        r.raise_for_status()
        d = r.json()
        for label, key in (("BTC", "bitcoin"), ("ETH", "ethereum")):
            if key in d:
                price = d[key]["usd"]
                chg = d[key].get("usd_24h_change", 0.0)
                arrow = "▲" if chg >= 0 else "▼"
                lines.append(f"{label}: ${price:,.0f} {arrow}{abs(chg):.2f}%")
    except Exception as e:
        print(f"markets: failed crypto: {e}", file=sys.stderr)

    return "\n".join(lines) if lines else "(markets data unavailable)"


def get_news(n=8):
    """Top headlines from Google News RSS (no key)."""
    feeds = [
        "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",       # world
        "https://news.google.com/rss?hl=en-TH&gl=TH&ceid=TH:en",        # Thailand
    ]
    headlines = []
    for f in feeds:
        try:
            parsed = feedparser.parse(f)
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


def write_brief(markets, news, date_str):
    prompt = f"""You are writing a short daily morning brief that will be sent as a LINE text message. \
Keep it under 1500 characters. Plain text only - no markdown, no asterisks, no '#' headers. \
Simple emoji and line breaks are fine. Be crisp, warm, and useful.

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
        die(f"Claude API error {r.status_code}: {r.text}")
    data = r.json()
    return "".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    ).strip()


def broadcast(text):
    text = text[:4900]  # LINE hard cap is 5000 chars
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
    brief = write_brief(markets, news, date_str)

    print("----- BRIEF -----")
    print(brief)
    print("-----------------")

    if DRY_RUN:
        print("DRY_RUN=1 -> not sending to LINE")
        return
    broadcast(brief)


if __name__ == "__main__":
    main()
