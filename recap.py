#!/usr/bin/env python3
"""
Medini Homes — Daily maintenance recap (Gemini-powered).

Fetches the last 100 messages from the team WhatsApp group via Green API,
passes the recent conversation to Gemini for intelligent classification into
Unresolved / Resolved / Pending, then posts the formatted recap back to the
same group. Output is in English.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

GREEN_ID = os.environ["GREEN_API_ID_INSTANCE"]
GREEN_TOKEN = os.environ["GREEN_API_TOKEN"]
CHAT_ID = os.environ["WHATSAPP_CHAT_ID"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "36"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

BASE = f"https://api.green-api.com/waInstance{GREEN_ID}"


def green_post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}/{path}/{GREEN_TOKEN}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_messages(count: int = 100) -> list:
    return green_post("getChatHistory", {"chatId": CHAT_ID, "count": count})


def message_text(m: dict) -> str:
    return (
        m.get("textMessage")
        or (m.get("extendedTextMessageData") or {}).get("text")
        or (m.get("extendedTextMessage") or {}).get("text")
        or m.get("caption")
        or ""
    )


def build_transcript(messages: list, lookback_hours: int) -> str:
    cutoff = time.time() - lookback_hours * 3600
    recent = [m for m in messages if m.get("timestamp", 0) >= cutoff]
    # Oldest first so the LLM reads the conversation in order
    recent.sort(key=lambda m: m.get("timestamp", 0))

    lines = []
    for m in recent:
        if m.get("typeMessage") == "reactionMessage":
            continue
        text = message_text(m).strip()
        if not text:
            continue
        ts = datetime.fromtimestamp(m.get("timestamp", 0), tz=timezone(timedelta(hours=4)))
        stamp = ts.strftime("%d-%m %H:%M")
        sender = m.get("senderName", "?")
        lines.append(f"[{stamp}] {sender}: {text}")
    return "\n".join(lines)


PROMPT = """You are a maintenance operations analyst for Medini Homes, a short-term \
rental management company in Dubai.

Below is the last {hours} hours of messages from the operations team WhatsApp \
group. Team members report maintenance issues per apartment, discuss fixes, \
and track follow-ups. Apartment identifiers are usually 2-4 digit numbers \
(e.g. 508, 1509, G11, 320 Azizi, 206 Marwa).

Your task: produce a structured English WhatsApp recap with THREE sections:

1. *UNRESOLVED ISSUES* — problems reported but NOT confirmed fixed yet. \
Include ongoing repairs, guest-reported issues awaiting action, and anything \
pending a service provider visit.
2. *RESOLVED ISSUES* — problems explicitly confirmed as fixed, completed, \
delivered, rectified, or sorted during the period.
3. *PENDING ACTION ITEMS* — follow-ups, scheduled visits, orders to place, \
emails/contracts to renew, information requested but not yet received, \
reminders for upcoming guest access windows.

Rules:
- One bullet per distinct issue. Merge messages that discuss the same issue.
- Start each bullet with the apartment in *bold* when identifiable, e.g. `*508 Marwa* — ...`.
- Keep bullets under 2 lines each. Be specific about the issue and the next step.
- Mention who reported or is handling the item in parentheses at the end when \
useful, e.g. `(WalterM / SEMAX)`.
- Use WhatsApp formatting: *bold* with single asterisks, _italic_ with underscores.
- If a section has no items, write `_None_` instead of leaving it empty.
- Do NOT invent issues. Only include things actually discussed in the transcript.
- Do NOT include greetings, shift start/end messages, or payment/invoice chatter \
unless directly tied to a maintenance item.

Output ONLY the final WhatsApp message text, starting with the header line:
`*MAINTENANCE & FOLLOW-UP RECAP — {date}*`

Then a blank line, then the three sections, then a blank line, then the \
footer `_Auto-generated from last {hours}h of group messages._`.

--- TRANSCRIPT START ---
{transcript}
--- TRANSCRIPT END ---
"""


def call_gemini(transcript: str, hours: int, date_str: str) -> str:
    prompt = PROMPT.format(hours=hours, date=date_str, transcript=transcript)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2048,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("Gemini error body:", e.read().decode()[:500], file=sys.stderr)
        raise

    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data}")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError(f"Gemini returned empty text: {data}")
    return text


def send_message(text: str) -> dict:
    return green_post("sendMessage", {"chatId": CHAT_ID, "message": text})


def main() -> int:
    print(f"Fetching chat history (lookback={LOOKBACK_HOURS}h)...", flush=True)
    messages = fetch_messages(100)
    print(f"Got {len(messages)} messages.", flush=True)

    transcript = build_transcript(messages, LOOKBACK_HOURS)
    if not transcript.strip():
        print("No recent messages. Nothing to do.")
        return 0
    print(f"Transcript length: {len(transcript)} chars.")

    today = datetime.now(timezone(timedelta(hours=4))).strftime("%d %B %Y")
    recap = call_gemini(transcript, LOOKBACK_HOURS, today)
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
