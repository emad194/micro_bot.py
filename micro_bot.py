import os
import re
import json
import time
import random
import asyncio
import sqlite3
import requests
import websockets
from nostr_sdk import Keys, EventBuilder, Tag, Kind
import sys

sys.stdout.reconfigure(line_buffering=True)

# ==================== الإعدادات ====================
NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

IMAGE_URL = "https://i.postimg.cc/1zv9VTqN/altqaaat.png"

MAX_REPLIES_PER_CYCLE = 10
SLEEP_BETWEEN_CYCLES = 20
DB_NAME = "nostr_replies.db"

GLOBAL_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://relay.nostr.band",
    "wss://nostr.wine",
    "wss://purplepag.es",
    "wss://relay.snort.social",
    "wss://eden.nostr.land",
    "wss://offchain.pub"
]

DYNAMIC_CLOSINGS = [
    f"🕊️ You can check our daily survival story & updates pinned at the top of my profile if you feel led to read.\n\n{IMAGE_URL}",
    f"🤍 We document our family's raw reality in Gaza on my pinned post if you'd like to take a look.\n\n{IMAGE_URL}",
    f"✨ Our personal journey of resilience is pinned on my profile—any zap or share helps us survive.\n\n{IMAGE_URL}",
    f"🌱 Quietly sharing our daily life amidst the ruins on my pinned note if you ever want to check.\n\n{IMAGE_URL}",
    f"🍉 If you have a moment, our story and campaign are pinned at the top of my page. Warm regards.\n\n{IMAGE_URL}"
]

# ==================== قاعدة البيانات ====================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS interactions (
            sender_pubkey TEXT PRIMARY KEY,
            event_id TEXT,
            created_at TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def is_interacted(pubkey):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT 1 FROM interactions WHERE sender_pubkey = ?", (pubkey.lower(),))
        row = c.fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False

def record_interaction(pubkey, event_id):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO interactions (sender_pubkey, event_id, created_at)
            VALUES (?, ?, datetime('now'))
        ''', (pubkey.lower(), event_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[!] DB Error: {e}")

# ==================== استخراج وتحليل الزاب ====================
def parse_bolt11_sats(bolt11_invoice):
    try:
        inv = str(bolt11_invoice).lower()
        if "lnbc" not in inv:
            return None
        raw = inv.split("lnbc")[1]
        m = re.match(r"^(\d+)([munp]?)", raw)
        if not m:
            return None
        amount, unit = int(m.group(1)), m.group(2)
        if unit == 'm':   return amount * 100_000
        elif unit == 'u': return amount * 100
        elif unit == 'n': return int(amount * 0.1)
        elif unit == 'p': return int(amount * 0.0001)
        return amount * 100_000_000
    except Exception:
        return None

def extract_zap_data(event_data):
    sender_pubkey = None
    target_event_id = None
    sats_amount = None

    for tag in event_data.get("tags", []):
        if len(tag) >= 2:
            k, v = str(tag[0]).lower(), str(tag[1])
            if k == 'bolt11':
                sats_amount = parse_bolt11_sats(v)
            elif k == 'e':
                target_event_id = v
            elif k == 'description':
                try:
                    desc_obj = json.loads(v)
                    if "pubkey" in desc_obj:
                        sender_pubkey = desc_obj["pubkey"]
                except Exception:
                    pass

    return sender_pubkey, target_event_id, sats_amount

async def query_user_meta(relay, pubkey_hex):
    name, post_id = None, None
    try:
        async with websockets.connect(relay, ping_interval=2, ping_timeout=2, open_timeout=2) as ws:
            req = json.dumps(["REQ", "u_meta", {"authors": [pubkey_hex], "kinds": [0, 1], "limit": 10}])
            await ws.send(req)
            for _ in range(12):
                resp = await asyncio.wait_for(ws.recv(), timeout=1.0)
                data = json.loads(resp)
                if data[0] == "EVENT" and len(data) >= 3:
                    ev = data[2]
                    if ev.get("kind") == 0 and not name:
                        content = json.loads(ev.get("content", "{}"))
                        n = content.get("display_name") or content.get("name")
                        if n:
                            clean = re.sub(r'[^\w\s]', '', n).strip().split()[0]
                            if len(clean) >= 3 and not clean.isupper():
                                name = clean.capitalize()
                    elif ev.get("kind") == 1 and not post_id:
                        post_id = ev.get("id")
                elif data[0] == "EOSE":
                    break
    except Exception:
        pass
    return name, post_id

async def fetch_user_meta_fast(pubkey_hex):
    tasks = [query_user_meta(r, pubkey_hex) for r in GLOBAL_RELAYS[:5]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    f_name, f_post = None, None
    for res in results:
        if isinstance(res, tuple):
            n, p = res
            if not f_name and n: f_name = n
            if not f_post and p: f_post = p
            if f_name and f_post: break
    return f_name, f_post

def generate_personalized_reply(sats_amount, user_name=None):
    greeting = f"Hi {user_name}," if user_name else "Hi,"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    hint = f"Active supporter who zaps creators around {sats_amount} sats." if sats_amount else "Active supporter."

    system_instruction = (
        "You are an authentic person surviving the hardships in Gaza, writing a warm, respectful public reply to a generous supporter on Nostr.\n\n"
        "TASK:\n"
        "Write exactly ONE sincere, natural sentence appreciating their generous warmth across Nostr, while gently introducing your family's daily reality in Gaza.\n\n"
        "RULES:\n"
        "1. Do NOT imply they zapped you directly.\n"
        "2. Do NOT include greetings or closings.\n"
        "3. Sound human, humble, and authentic in English."
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Context: {hint}"}
        ],
        "temperature": 0.8
    }

    try:
        res = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=8)
        if res.status_code == 200:
            text = res.json()["choices"][0]["message"]["content"].strip().replace('"', '')
            if len(text) > 15:
                closing = random.choice(DYNAMIC_CLOSINGS)
                return f"{greeting} {text}\n\n{closing}"
    except Exception:
        pass

    closing = random.choice(DYNAMIC_CLOSINGS)
    return f"{greeting} Seeing your generous warmth across Nostr brings genuine hope to our family amidst the ongoing hardships in Gaza.\n\n{closing}"

async def fetch_recent_zaps():
    seen_ids = set()
    events = []
    since_timestamp = int(time.time()) - (24 * 3600)

    async def get_zaps(relay):
        local_evs = []
        try:
            async with websockets.connect(relay, ping_interval=2, ping_timeout=2, open_timeout=2) as ws:
                await ws.send(json.dumps(["REQ", "sub_zap", {"kinds": [9735], "since": since_timestamp, "limit": 100}]))
                for _ in range(40):
                    resp = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(resp)
                    if data[0] == "EVENT" and len(data) >= 3:
                        local_evs.append(data[2])
                    elif data[0] == "EOSE":
                        break
        except Exception:
            pass
        return local_evs

    results = await asyncio.gather(*(get_zaps(r) for r in GLOBAL_RELAYS[:6]), return_exceptions=True)
    for batch in results:
        if isinstance(batch, list):
            for ev in batch:
                eid = ev.get("id")
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    events.append(ev)
    return events

async def broadcast_signed_event(event_dict):
    msg = json.dumps(["EVENT", event_dict])
    async def send(r):
        try:
            async with websockets.connect(r, ping_interval=2, ping_timeout=2, open_timeout=2) as ws:
                await ws.send(msg)
        except Exception:
            pass
    await asyncio.gather(*(send(r) for r in GLOBAL_RELAYS), return_exceptions=True)

async def run_cycle(keys):
    bot_hex = keys.public_key().to_hex().lower()
    print("[*] Fetching fresh live Zaps across Nostr network...")
    events = await fetch_recent_zaps()
    print(f"[*] Retrieved {len(events)} total zaps.")

    replied = 0
    for ev in events:
        if replied >= MAX_REPLIES_PER_CYCLE:
            break

        sender_hex, target_event_id, sats = extract_zap_data(ev)
        if not sender_hex:
            continue

        sender_hex = sender_hex.lower()
        if sender_hex == bot_hex or is_interacted(sender_hex):
            continue

        user_name, last_post_id = await fetch_user_meta_fast(sender_hex)
        target_post = last_post_id or target_event_id
        if not target_post:
            continue

        reply_content = await asyncio.to_thread(generate_personalized_reply, sats, user_name)
        if not reply_content:
            continue

        try:
            tags = [
                Tag.parse(["e", target_post, "", "root"]),
                Tag.parse(["p", sender_hex])
            ]

            # متوافق بالكامل مع nostr-sdk 0.45.x
            builder = EventBuilder(Kind(1), reply_content, tags)
            signed_event = builder.to_event(keys)
            event_json = json.loads(signed_event.as_json())

            await broadcast_signed_event(event_json)
            record_interaction(sender_hex, target_post)
            replied += 1

            print(f"\n[✓] Published Reply #{replied} to @{user_name or sender_hex[:8]} [{sats or 'Active'} Sats]")
            await asyncio.sleep(random.uniform(2.5, 4.5))

        except Exception as err:
            print(f"[!] Send error: {err}")

    print(f"[*] Cycle finished. Total newly published: {replied}")

async def main():
    init_db()
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("[!] Missing NOSTR_NSEC or DEEPSEEK_API_KEY in Environment.")
        return

    try:
        keys = Keys.parse(NOSTR_SECRET)
    except Exception as e:
        print(f"[!] Invalid Nostr Key: {e}")
        return

    print("=== Nostr Global Live Engagement Engine Started ===")
    while True:
        try:
            await run_cycle(keys)
        except Exception as e:
            print(f"[!] Cycle exception: {e}")

        print(f"[*] Sleeping {SLEEP_BETWEEN_CYCLES}s before next fresh scan...\n")
        await asyncio.sleep(SLEEP_BETWEEN_CYCLES)

if __name__ == "__main__":
    asyncio.run(main())
