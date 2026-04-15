#!/usr/bin/env python3
"""
Medini Homes — Daily maintenance recap.

Fetches the last 100 messages from the team WhatsApp group via Green API,
classifies maintenance items from the last N days into Unresolved / Resolved /
Pending, formats a structured recap, and posts it back to the same group.

Runs on GitHub Actions. All credentials come from environment variables.
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

GREEN_ID = os.environ["GREEN_API_ID_INSTANCE"]
GREEN_TOKEN = os.environ["GREEN_API_TOKEN"]
CHAT_ID = os.environ["WHATSAPP_CHAT_ID"]
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "36"))

BASE = f"https://api.green-api.com/waInstance{GREEN_ID}"


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}/{path}/{GREEN_TOKEN}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_messages(count: int = 100) -> list:
    return post("getChatHistory", {"chatId": CHAT_ID, "count": count})


def message_text(m: dict) -> str:
    return (
        m.get("textMessage")
        or (m.get("extendedTextMessageData") or {}).get("text")
        or (m.get("extendedTextMessage") or {}).get("text")
        or m.get("caption")
        or ""
    )


APARTMENT_RE = re.compile(r"\b([GB]?\d{2,4}[A-Za-z]?)\b")

RESOLVED_KEYWORDS = [
    "resolved", "rectified", "completed", "done", "fixed", "delivered",
    "sorted", "issued", "has been", "cleaning done", "cleaning is done",
]
UNRESOLVED_KEYWORDS = [
    "still leaking", "still", "clogged", "not working", "broken", "leaking",
    "issue", "problem", "fault", "coming out", "dirty", "stain",
    "under maintenance", "ongoing", "on going",
]
PENDING_KEYWORDS = [
    "waiting", "please", "kindly", "pending", "tomorrow", "later",
    "scheduled", "available", "follow up", "will", "please note", "advise",
    "reminder", "send", "order", "request",
]


def classify(text: str) -> str:
    low = text.lower()
    if any(k in low for k in RESOLVED_KEYWORDS):
        return "resolved"
    if any(k in low for k in UNRESOLVED_KEYWORDS):
        return "unresolved"
    if any(k in low for k in PENDING_KEYWORDS):
        return "pending"
    return "other"


def extract_apartment(text: str) -> str:
    m = APARTMENT_RE.search(text)
    return m.group(1) if m else ""


def build_recap(messages: list, lookback_hours: int) -> str:
    cutoff = time.time() - lookback_hours * 3600
    recent = [m for m in messages if m.get("timestamp", 0) >= cutoff]

    buckets = {"unresolved": [], "resolved": [], "pending": []}
    seen = set()

    for m in recent:
        text = message_text(m).strip()
        if not text or len(text) < 8:
            continue
        # Skip pure greetings / reactions
        low = text.lower()
        if low.startswith(("good morning", "good afternoon", "thank you", "noted", "oky")):
            continue
        if m.get("typeMessage") == "reactionMessage":
            continue

        category = classify(text)
        if category == "other":
            continue

        apt = extract_apartment(text)
        sender = m.get("senderName", "?")
        # Collapse at 260 chars
        snippet = text.replace("\n", " ").strip()
        if len(snippet) > 260:
            snippet = snippet[:257] + "..."

        key = (category, apt, snippet[:60])
        if key in seen:
            continue
        seen.add(key)

        prefix = f"*{apt}* — " if apt else ""
        buckets[category].append(f"{prefix}{snippet} _(reported: {sender})_")

    today = datetime.now(timezone(timedelta(hours=4))).strftime("%d %B %Y")  # Dubai time
    lines = [f"*MAINTENANCE & FOLLOW-UP RECAP — {today}*", ""]

    def section(title: str, items: list, limit: int = 15):
        lines.append(f"*{title}*")
        if not items:
            lines.append("_None_")
        else:
            for it in items[:limit]:
                lines.append(f"• {it}")
        lines.append("")

    section("UNRESOLVED ISSUES", buckets["unresolved"])
    section("RESOLVED ISSUES", buckets["resolved"])
    section("PENDING ACTION ITEMS", buckets["pending"])

    lines.append(f"_Auto-generated from last {lookback_hours}h of group messages._")
    return "\n".join(lines)


def send_message(text: str) -> dict:
    return post("sendMessage", {"chatId": CHAT_ID, "message": text})


def main() -> int:
    print(f"Fetching chat history (lookback={LOOKBACK_HOURS}h)...", flush=True)
    messages = fetch_messages(100)
    print(f"Got {len(messages)} messages.", flush=True)

    recap = build_recap(messages, LOOKBACK_HOURS)
    print("---- RECAP ----")
    print(recap)
    print("---- /RECAP ----")

    resp = send_message(recap)
    print("Send response:", resp, flush=True)
    if "idMessage" not in resp:
        print("ERROR: no idMessage in response", file=sys.stderr)
        return 1
    print(f"OK. idMessage={resp['idMessage']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
