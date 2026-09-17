#!/usr/bin/env python3
"""Daily research-driven investment briefing -> LINE Official Account broadcast.

Uses the Anthropic Messages API WEB-SEARCH tool so Claude actually searches the
web (last 24-48h) and writes a structured analyst briefing, broadcast to LINE.

Required env (GitHub Actions secrets):
  ANTHROPIC_API_KEY          - Claude API key with billing + web search enabled
  LINE_CHANNEL_ACCESS_TOKEN  - long-lived token from the LINE Developers console
Optional env:
  ANTHROPIC_MODEL, BRIEF_TIMEZONE (default Asia/Bangkok), MAX_SEARCHES (10), DRY_RUN=1
"""

import os
import sys
import datetime
import zoneinfo

import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
TZ = os.environ.get("BRIEF_TIMEZONE", "Asia/Bangkok")
MAX_SEARCHES = int(os.environ.get("MAX_SEARCHES", "10"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


if not ANTHROPIC_API_KEY:
    die("ANTHROPIC_API_KEY is not set")
if not LINE_TOKEN and not DRY_RUN:
    die("LINE_CHANNEL_ACCESS_TOKEN is not set")


def build_prompt(date_str):
    return f"""You are my personal financial news researcher. Today is {date_str}.

Use the web_search tool to research the most important developments from the LAST 24-48 HOURS, then write a structured daily market briefing for an investment analyst. Search across these beats (run several targeted searches, and open key results):
- US macro & the Federal Reserve: rate decisions/outlook, inflation (CPI/PCE), labor market, Treasury yields (cite 2Y/10Y levels and bp moves).
- US equities: Dow/S&P 500/Nasdaq closing levels and % changes, sector rotation, and 3-5 notable large-cap movers with the reason.
- Commodities, FX & crypto: gold, oil (WTI/Brent), the US dollar (DXY), and Bitcoin, with levels.
- Global energy developments: OPEC+, sanctions/supply, natural gas/LNG, refining margins/crack spreads, petrochemicals, and the energy transition.
- Thailand: the SET Index level and change, turnover, key Thai stock/sector movers and policy news. Prefer Thai sources such as Kaohoon (kaohoon.com / kaohooninternational.com), Bangkok Post, and the Stock Exchange of Thailand.
- Geopolitics & other global macro that moves markets.

RULES:
- Be factual and concise. Prioritize the last 24-48 hours; note the "as of" date/session.
- Use ONLY figures you found via search; never invent numbers. If something isn't found, say so briefly.
- Explain WHY things moved and the read-through for growth/tech stocks, gold, the US dollar, and Thai energy/refiner/petrochem names (PTT, PTTEP, TOP, SPRC, IRPC, PTTGC, IVL) where relevant.

OUTPUT FORMAT (this is sent as a LINE text message, so PLAIN TEXT ONLY - no markdown, no asterisks, no '#'. Use emoji as section markers and line breaks; use '•' for bullets). Target 3500-6000 characters. Sections in order:
1) Header line: 📈 emoji + date, then a 2-3 sentence executive summary (the single biggest takeaway).
2) 🏦 Fed & rates
3) 📊 US equities
4) 🔥 Stocks in focus (the movers)
5) 🪙 Commodities / FX / crypto
6) 🌍 Global energy developments
7) 🤖 Tech / AI
8) 🇹🇭 Thailand
9) 🌐 Geopolitics & macro
10) 👀 Watch next 24h
11) A final one-line sources note listing the outlet names used, then: "Not investment advice."

Write ONLY the briefing text - no preamble, no explanation of your process."""


def research_briefing(date_str):
    payload = {
        "model": MODEL,
        "max_tokens": 4000,
        "tools": [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": MAX_SEARCHES,
            }
        ],
        "messages": [{"role": "user", "content": build_prompt(date_str)}],
    }
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=300,
    )
    if r.status_code != 200:
        die(f"Claude API error {r.status_code}: {r.text}")
    body = r.json()
    content = body.get("content", [])
    # Take only the text written AFTER the last web-search result, so any
    # intermediate "let me search..." preamble is excluded.
    last_tool = -1
    for i, b in enumerate(content):
        if b.get("type") in ("web_search_tool_result", "server_tool_use"):
            last_tool = i
    final_blocks = content[last_tool + 1:] if last_tool >= 0 else content
    text = "\n".join(
        b.get("text", "").strip()
        for b in final_blocks
        if b.get("type") == "text" and b.get("text", "").strip()
    ).strip()
    if not text:
        text = "\n".join(
            b.get("text", "").strip()
            for b in content
            if b.get("type") == "text" and b.get("text", "").strip()
        ).strip()
    if not text:
        die("Claude returned no briefing text")
    return text


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

    briefing = research_briefing(date_str)

    print("----- BRIEFING -----")
    print(briefing)
    print("--------------------")

    if DRY_RUN:
        print("DRY_RUN=1 -> not sending to LINE")
        return
    broadcast(briefing)


if __name__ == "__main__":
    main()
