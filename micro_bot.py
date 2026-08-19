import os
import re
import json
import time
import hashlib
import random
import asyncio
import requests
import websockets
from nostr_sdk import Keys, PublicKey
import sys

sys.stdout.reconfigure(line_buffering=True)

NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_REPLIES_PER_CYCLE = 10
SLEEP_BETWEEN_CYCLES = 180

# سجل تراكمي دائم لا يُمسح بين الدورات لمنع تكرار أي شخص أو أي منشور نهائياً
GLOBAL_SEEN_SENDERS = set()
GLOBAL_REPLIED_EVENTS = set()

GLOBAL_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://relay.nostr.band",
    "wss://relay.snort.social",
    "wss://purplepag.es",
    "wss://nostr.wine",
    "wss://relay.current.fyi"
]

DYNAMIC_CLOSINGS = [
    "🕊️ You can check our daily survival story & updates pinned at the top of my profile if you feel led to read.",
    "🤍 We document our family's raw reality in Gaza on my pinned post if you'd like to take a look.",
    "✨ Our personal journey of resilience is pinned on my profile—any zap or share helps us survive.",
    "🌱 Quietly sharing our daily life amidst the ruins on my pinned note if you ever want to check.",
    "🍉 If you have a moment, our story and campaign are pinned at the top of my page. Warm regards."
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
    if len(clean) < 3 or len(clean) > 15:
        return False
    if clean.isupper():
        return False
    project_keywords = ["bot", "house", "media", "relay", "shop", "news", "app", "team", "club", "hub", "node", "pay"]
    if any(kw in clean.lower() for kw in project_keywords):
        return False
    return True

async def fetch_user_meta_ws(pubkey_hex):
    name = None
    last_post_id = None
    for relay in GLOBAL_RELAYS[:3]:
        try:
            async with websockets.connect(relay, ping_interval=5, ping_timeout=5) as ws:
                req_profile = json.dumps(["REQ", "meta", {"authors": [pubkey_hex], "kinds": [0, 1], "limit": 4}])
                await ws.send(req_profile)
                for _ in range(6):
                    resp = await asyncio.wait_for(ws.recv(), timeout=1.8)
                    data = json.loads(resp)
                    if data[0] == "EVENT" and len(data) >= 3:
                        ev = data[2]
                        if ev.get("kind") == 0 and not name:
                            content = json.loads(ev.get("content", "{}"))
                            name_val = content.get("display_name") or content.get("name")
                            if name_val and len(name_val.strip()) > 0:
                                clean = re.sub(r'[^\w\s]', '', name_val).strip()
                                if clean:
                                    first_word = clean.split()[0]
                                    if is_valid_human_name(first_word):
                                        name = first_word.capitalize()
                        elif ev.get("kind") == 1 and not last_post_id:
                            last_post_id = ev.get("id")
                    elif data[0] == "EOSE":
                        break
            if name and last_post_id:
                break
        except Exception:
            continue
    return name, last_post_id

def generate_personalized_reply(sats_amount, user_name=None):
    greeting = f"Hi {user_name}," if user_name else "Hi,"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    
    amount_hint = f"They are an active supporter who frequently zaps community creators on Nostr (around {sats_amount} sats)." if sats_amount else "They are active in supporting community creators with zaps on Nostr."
    
    system_instruction = (
        "You are an authentic person surviving the hardships in Gaza, writing a warm, respectful public reply to a generous supporter on Nostr.\n\n"
        "TASK:\n"
        "Write exactly ONE sincere, natural sentence appreciating their generous warmth and active support/zaps across the Nostr community, while gently introducing your family's daily reality in Gaza with dignity.\n\n"
        "RULES:\n"
        "1. Do NOT imply they zapped you directly (they zapped creators across Nostr).\n"
        "2. Do NOT include greetings (like Hi, Hello) or closings/links (added automatically).\n"
        "3. Sound completely human, humble, and authentic.\n"
        "4. Write in clean, fluent English."
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Context: {amount_hint}. Write the single sentence."}
        ],
        "temperature": 0.8
    }

    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            res_text = response.json()["choices"][0]["message"]["content"].strip().replace('"', '')
            if len(res_text) > 15:
                closing = random.choice(DYNAMIC_CLOSINGS)
                return f"{greeting} {res_text}\n\n{closing}"
    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")

    return (
        f"{greeting} Seeing your generous warmth and active support across Nostr brings genuine hope. "
        f"My family and I are enduring critical hardships in Gaza right now.\n\n"
        f"{random.choice(DYNAMIC_CLOSINGS)}"
    )

async def fetch_recent_zaps_ws():
    events = []
    seen_ids = set()
    for relay in GLOBAL_RELAYS[:5]:
        try:
            async with websockets.connect(relay, ping_interval=5, ping_timeout=5) as ws:
                req = json.dumps(["REQ", "zaps_sub", {"kinds": [9735], "limit": 60}])
                await ws.send(req)
                for _ in range(50):
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=1.5)
                        data = json.loads(resp)
                        if data[0] == "EVENT" and len(data) >= 3:
                            ev = data[2]
                            ev_id = ev.get("id")
                            if ev_id not in seen_ids:
                                seen_ids.add(ev_id)
                                events.append(ev)
                        elif data[0] == "EOSE":
                            break
                    except asyncio.TimeoutError:
                        break
        except Exception:
            continue
    return events

def create_and_sign_raw_event(keys, kind, content, tags):
    pubkey = keys.public_key().to_hex()
    created_at = int(time.time())
    
    serialized = json.dumps([
        0,
        pubkey,
        created_at,
        kind,
        tags,
        content
    ], separators=(',', ':'), ensure_ascii=False)
    
    event_id = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
    
    try:
        sig = keys.sign_schnorr(bytes.fromhex(event_id))
    except Exception:
        sig = keys.secret_key().sign_schnorr(bytes.fromhex(event_id))

    sig_hex = sig.to_hex() if hasattr(sig, "to_hex") else str(sig)

    return {
        "id": event_id,
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig_hex
    }

async def broadcast_signed_event_ws(event_dict):
    msg = json.dumps(["EVENT", event_dict])
    for relay in GLOBAL_RELAYS:
        try:
            async with websockets.connect(relay, ping_interval=3, ping_timeout=3) as ws:
                await ws.send(msg)
        except Exception:
            pass

async def run_single_cycle():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets in GitHub.")
        return

    try:
        keys = Keys.parse(NOSTR_SECRET)
    except Exception as e:
        print(f"Error parsing keys: {e}")
        return

    bot_pk = keys.public_key()
    bot_hex = bot_pk.to_hex().lower()

    print("Scanning Nostr network for active Zap supporters via WebSocket...")
    events = await fetch_recent_zaps_ws()
    print(f"Fetched {len(events)} zap events.")

    if not events:
        return

    replies_sent = 0

    for ev in events:
        if replies_sent >= MAX_REPLIES_PER_CYCLE:
            break

        sender_hex, target_event_id, sats = extract_zap_data(ev)
        if not sender_hex:
            continue

        sender_hex = sender_hex.lower()
        # تخطي الحساب إذا كان حساب البوت أو تم الرد عليه مسبقاً
        if sender_hex == bot_hex or sender_hex in GLOBAL_SEEN_SENDERS:
            continue

        user_name, last_post_id = await fetch_user_meta_ws(sender_hex)
        event_to_reply = target_event_id or last_post_id
        if not event_to_reply:
            continue

        # تخطي المنشور إذا تم الرد عليه من قبل لمنع تكرار الردود في نفس الثريد
        if event_to_reply in GLOBAL_REPLIED_EVENTS:
            continue

        reply_text = await asyncio.to_thread(generate_personalized_reply, sats, user_name)
        if not reply_text:
            continue

        try:
            tags = [
                ["e", event_to_reply, "", "root"],
                ["e", event_to_reply, "", "reply"],
                ["p", sender_hex]
            ]

            signed_event = create_and_sign_raw_event(keys, 1, reply_text, tags)
            await broadcast_signed_event_ws(signed_event)

            # تسجيل الشخص والمنشور في السجل التراكمي الدائم لمنع التكرار نهائياً
            GLOBAL_SEEN_SENDERS.add(sender_hex)
            GLOBAL_REPLIED_EVENTS.add(event_to_reply)

            replies_sent += 1
            print(f"-> Published Public Reply #{replies_sent} to {user_name or 'Supporter'} [{sats or 'Active'} Sats]:")
            print(f"\"{reply_text}\"\n" + "-"*50)

            if replies_sent < MAX_REPLIES_PER_CYCLE:
                wait_secs = random.randint(3, 6)
                await asyncio.sleep(wait_secs)

        except Exception as send_err:
            print(f"Notice publishing reply: {send_err}")

    print(f"Cycle finished: Published {replies_sent} unique public replies.")

async def main():
    print("Starting Nostr Targeted Public Reply Engine (Anti-Duplicate)...")
    cycle = 0

    while True:
        cycle += 1
        print(f"\n--- Starting Cycle #{cycle} ---")
        try:
            await run_single_cycle()
        except Exception as e:
            print(f"Error in cycle execution: {e}")

        print(f"Waiting 3 minutes ({SLEEP_BETWEEN_CYCLES}s) before next scan...")
        await asyncio.sleep(SLEEP_BETWEEN_CYCLES)

if __name__ == "__main__":
    asyncio.run(main())
