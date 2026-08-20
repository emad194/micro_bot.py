import os
import re
import json
import time
import hashlib
import random
import asyncio
import requests
import websockets
from nostr_sdk import Keys
import sys

sys.stdout.reconfigure(line_buffering=True)

# ==================== الإعدادات والمفاتيح ====================
NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

# رابط الصورة المباشر
IMAGE_URL = "https://i.postimg.cc/1zv9VTqN/altqaaat.png"

# تسريع الدورة وتقليل فترات الانتظار
MAX_REPLIES_PER_CYCLE = 15
SLEEP_BETWEEN_CYCLES = 25  # راحة 25 ثانية فقط بين كل دورة فحص

GLOBAL_SEEN_SENDERS = set()
GLOBAL_REPLIED_EVENTS = set()

GLOBAL_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://relay.nostr.band",
    "wss://purplepag.es",
    "wss://nostr.wine"
]

DYNAMIC_CLOSINGS = [
    f"🕊️ You can check our daily survival story & updates pinned at the top of my profile if you feel led to read.\n\n{IMAGE_URL}",
    f"🤍 We document our family's raw reality in Gaza on my pinned post if you'd like to take a look.\n\n{IMAGE_URL}",
    f"✨ Our personal journey of resilience is pinned on my profile—any zap or share helps us survive.\n\n{IMAGE_URL}",
    f"🌱 Quietly sharing our daily life amidst the ruins on my pinned note if you ever want to check.\n\n{IMAGE_URL}",
    f"🍉 If you have a moment, our story and campaign are pinned at the top of my page. Warm regards.\n\n{IMAGE_URL}"
]

def parse_bolt11_sats(bolt11_invoice):
    try:
        invoice_lower = str(bolt11_invoice).lower()
        if "lnbc" in invoice_lower:
            parts = invoice_lower.split("lnbc")[1]
            num_str = ""
            for ch in parts:
                if ch.isdigit():
                    num_str += ch
                else:
                    break
            if num_str:
                return int(num_str)
    except Exception:
        pass
    return None

def extract_zap_data(event_data):
    sender_pubkey = None
    target_event_id = None
    sats_amount = None

    for tag in event_data.get("tags", []):
        if len(tag) >= 2:
            key = str(tag[0]).lower()
            val = str(tag[1])

            if key == 'bolt11':
                sats_amount = parse_bolt11_sats(val)
            elif key == 'e':
                target_event_id = val
            elif key == 'description':
                try:
                    desc_obj = json.loads(val)
                    if "pubkey" in desc_obj:
                        sender_pubkey = desc_obj["pubkey"]
                except Exception:
                    pass

    return sender_pubkey, target_event_id, sats_amount

def is_valid_human_name(raw_name):
    if not raw_name:
        return False
    clean = re.sub(r'[^a-zA-Z]', '', raw_name).strip()
    if len(clean) < 3 or len(clean) > 15 or clean.isupper():
        return False
    project_keywords = ["bot", "house", "media", "relay", "shop", "news", "app", "team", "club", "hub", "node", "pay"]
    return not any(kw in clean.lower() for kw in project_keywords)

async def query_relay_single_user(relay, pubkey_hex):
    name, last_post_id = None, None
    try:
        async with websockets.connect(relay, ping_interval=2, ping_timeout=2, open_timeout=1.5) as ws:
            req_profile = json.dumps(["REQ", "meta", {"authors": [pubkey_hex], "kinds": [0, 1], "limit": 2}])
            await ws.send(req_profile)
            for _ in range(4):
                resp = await asyncio.wait_for(ws.recv(), timeout=0.8)
                data = json.loads(resp)
                if data[0] == "EVENT" and len(data) >= 3:
                    ev = data[2]
                    if ev.get("kind") == 0 and not name:
                        content = json.loads(ev.get("content", "{}"))
                        name_val = content.get("display_name") or content.get("name")
                        if name_val:
                            clean = re.sub(r'[^\w\s]', '', name_val).strip()
                            if clean and is_valid_human_name(clean.split()[0]):
                                name = clean.split()[0].capitalize()
                    elif ev.get("kind") == 1 and not last_post_id:
                        last_post_id = ev.get("id")
                elif data[0] == "EOSE":
                    break
    except Exception:
        pass
    return name, last_post_id

async def fetch_user_meta_fast(pubkey_hex):
    tasks = [query_relay_single_user(relay, pubkey_hex) for relay in GLOBAL_RELAYS[:3]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    final_name, final_post_id = None, None
    for res in results:
        if isinstance(res, tuple):
            n, p = res
            if not final_name and n: final_name = n
            if not final_post_id and p: final_post_id = p
            if final_name and final_post_id: break
            
    return final_name, final_post_id

def generate_personalized_reply(sats_amount, user_name=None):
    greeting = f"Hi {user_name}," if user_name else "Hi,"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    amount_hint = f"Active supporter who zaps creators around {sats_amount} sats." if sats_amount else "Active Nostr supporter."
    
    system_instruction = (
        "You are an authentic person surviving the hardships in Gaza, writing a warm, respectful public reply to a generous supporter on Nostr.\n\n"
        "TASK:\n"
        "Write exactly ONE sincere, natural sentence appreciating their generous warmth and active support/zaps across the Nostr community, while gently introducing your family's daily reality in Gaza with dignity.\n\n"
        "RULES:\n"
        "1. Do NOT imply they zapped you directly.\n"
        "2. Do NOT include greetings or closings (added automatically).\n"
        "3. Sound human, humble, and authentic in English."
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Context: {amount_hint}."}
        ],
        "temperature": 0.8
    }

    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=6)
        if response.status_code == 200:
            res_text = response.json()["choices"][0]["message"]["content"].strip().replace('"', '')
            if len(res_text) > 15:
                closing = random.choice(DYNAMIC_CLOSINGS)
                return f"{greeting} {res_text}\n\n{closing}"
    except Exception:
        pass

    closing = random.choice(DYNAMIC_CLOSINGS)
    return (
        f"{greeting} Seeing your generous warmth across Nostr brings genuine hope to our family amidst the ongoing hardships in Gaza.\n\n"
        f"{closing}"
    )

async def fetch_recent_zaps_fast():
    events = []
    seen_ids = set()

    async def get_zaps(relay):
        local_evs = []
        try:
            async with websockets.connect(relay, ping_interval=2, ping_timeout=2, open_timeout=1.5) as ws:
                await ws.send(json.dumps(["REQ", "zaps_sub", {"kinds": [9735], "limit": 120}]))
                for _ in range(50):
                    resp = await asyncio.wait_for(ws.recv(), timeout=0.8)
                    data = json.loads(resp)
                    if data[0] == "EVENT" and len(data) >= 3:
                        local_evs.append(data[2])
                    elif data[0] == "EOSE":
                        break
        except Exception:
            pass
        return local_evs

    results = await asyncio.gather(*(get_zaps(r) for r in GLOBAL_RELAYS[:4]), return_exceptions=True)
    for batch in results:
        if isinstance(batch, list):
            for ev in batch:
                ev_id = ev.get("id")
                if ev_id and ev_id not in seen_ids:
                    seen_ids.add(ev_id)
                    events.append(ev)
    return events

def create_and_sign_raw_event(keys, kind, content, tags):
    pubkey = keys.public_key().to_hex()
    created_at = int(time.time())
    serialized = json.dumps([0, pubkey, created_at, kind, tags, content], separators=(',', ':'), ensure_ascii=False)
    event_id = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
    
    try:
        sig = keys.sign_schnorr(bytes.fromhex(event_id))
    except Exception:
        sig = keys.secret_key().sign_schnorr(bytes.fromhex(event_id))

    sig_hex = sig.to_hex() if hasattr(sig, "to_hex") else str(sig)
    return {
        "id": event_id, "pubkey": pubkey, "created_at": created_at,
        "kind": kind, "tags": tags, "content": content, "sig": sig_hex
    }

async def send_to_single_relay(relay, msg):
    try:
        async with websockets.connect(relay, ping_interval=2, ping_timeout=2, open_timeout=1.5) as ws:
            await ws.send(msg)
    except Exception:
        pass

async def broadcast_signed_event_fast(event_dict):
    msg = json.dumps(["EVENT", event_dict])
    await asyncio.gather(*(send_to_single_relay(r, msg) for r in GLOBAL_RELAYS), return_exceptions=True)

async def run_single_cycle():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing API keys in Environment.")
        return

    try:
        keys = Keys.parse(NOSTR_SECRET)
    except Exception as e:
        print(f"Key error: {e}")
        return

    bot_hex = keys.public_key().to_hex().lower()

    print("Fetching active zaps in parallel across relays...")
    events = await fetch_recent_zaps_fast()
    print(f"Fetched {len(events)} zaps rapidly.")

    replies_sent = 0

    for ev in events:
        if replies_sent >= MAX_REPLIES_PER_CYCLE:
            break

        sender_hex, target_event_id, sats = extract_zap_data(ev)
        if not sender_hex:
            continue

        sender_hex = sender_hex.lower()
        if sender_hex == bot_hex or sender_hex in GLOBAL_SEEN_SENDERS:
            continue

        user_name, last_post_id = await fetch_user_meta_fast(sender_hex)
        event_to_reply = target_event_id or last_post_id
        if not event_to_reply or event_to_reply in GLOBAL_REPLIED_EVENTS:
            continue

        reply_text = await asyncio.to_thread(generate_personalized_reply, sats, user_name)
        if not reply_text:
            continue

        try:
            tags = [
                ["e", event_to_reply, "", "root"],
                ["e", event_to_reply, "", "reply"],
                ["p", sender_hex],
                ["r", IMAGE_URL]  # وسوم الصورة لعرض المعاينة التلقائية في نوستر
            ]

            signed_event = create_and_sign_raw_event(keys, 1, reply_text, tags)
            await broadcast_signed_event_fast(signed_event)

            GLOBAL_SEEN_SENDERS.add(sender_hex)
            GLOBAL_REPLIED_EVENTS.add(event_to_reply)

            replies_sent += 1
            print(f"-> Published Public Reply #{replies_sent} to {user_name or 'Supporter'} [{sats or 'Active'} Sats]")
            print(f"\"{reply_text}\"\n" + "-"*50)

            # فاصل زمني سريع جداً (ثانية إلى ثانيتين فقط)
            await asyncio.sleep(random.uniform(1.0, 2.0))

        except Exception as send_err:
            print(f"Send notice: {send_err}")

    print(f"Cycle completed: {replies_sent} replies published.")

async def main():
    print("Starting Turbo Nostr Engagement Engine with Image Support...")
    cycle = 0
    while True:
        cycle += 1
        print(f"\n--- Cycle #{cycle} ---")
        try:
            await run_single_cycle()
        except Exception as e:
            print(f"Cycle error: {e}")

        print(f"Quick rest for {SLEEP_BETWEEN_CYCLES}s before next scan...")
        await asyncio.sleep(SLEEP_BETWEEN_CYCLES)

if __name__ == "__main__":
    asyncio.run(main())
