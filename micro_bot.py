import os
import re
import json
import random
import asyncio
import requests
import websockets
from nostr_sdk import (
    Keys, EventBuilder, PublicKey, Client, Kind, Tag,
    NostrSigner, nip04_encrypt
)
import sys

sys.stdout.reconfigure(line_buffering=True)

NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_DMS_PER_CYCLE = 6
SLEEP_BETWEEN_CYCLES = 300

GLOBAL_RELAYS = [
    "wss://relay.nostr.band",
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://relay.snort.social"
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

def extract_zap_sender(event_data):
    sender_pubkey = None
    sats_amount = None

    for tag in event_data.get("tags", []):
        if len(tag) >= 2:
            key = str(tag[0]).lower()
            val = str(tag[1])

            if key == 'bolt11':
                sats_amount = parse_bolt11_sats(val)
            elif key == 'description':
                try:
                    desc_obj = json.loads(val)
                    if "pubkey" in desc_obj:
                        sender_pubkey = desc_obj["pubkey"]
                except Exception:
                    pass

    return sender_pubkey, sats_amount

async def fetch_user_name_ws(pubkey_hex):
    for relay in GLOBAL_RELAYS[:2]:
        try:
            async with websockets.connect(relay, ping_interval=10, ping_timeout=10) as ws:
                req = json.dumps(["REQ", "get_name", {"authors": [pubkey_hex], "kinds": [0], "limit": 1}])
                await ws.send(req)
                for _ in range(5):
                    resp = await asyncio.wait_for(ws.recv(), timeout=3)
                    data = json.loads(resp)
                    if data[0] == "EVENT" and len(data) >= 3:
                        content = json.loads(data[2].get("content", "{}"))
                        name = content.get("display_name") or content.get("name")
                        if name and len(name.strip()) > 0:
                            clean_name = re.sub(r'[^\w\s]', '', name).strip()
                            if clean_name:
                                return clean_name.split()[0]
                    elif data[0] == "EOSE":
                        break
        except Exception:
            continue
    return None

def generate_personalized_dm(sats_amount, user_name=None):
    greeting = f"Hi {user_name}," if user_name else "Hi,"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    
    amount_hint = f"They recently sent a zap of {sats_amount} sats." if sats_amount else "They are active in supporting creators with zaps on Nostr."
    
    system_instruction = (
        "You are an authentic person surviving the hardships in Gaza, sending a warm, respectful direct message to a supporter on Nostr.\n\n"
        "TASK:\n"
        "Write exactly ONE or TWO sincere sentences thanking them for their active warmth/support across Nostr and introducing your family's reality with dignity.\n\n"
        "RULES:\n"
        "1. Do NOT include greetings (like Hi, Hello) or closings/links (they are handled automatically).\n"
        "2. Sound completely human, humble, and authentic.\n"
        "3. Write in clean, fluent English."
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Context: {amount_hint}. Write the 2 sentences."}
        ],
        "temperature": 0.8
    }

    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            res_text = response.json()["choices"][0]["message"]["content"].strip().replace('"', '')
            if len(res_text) > 15:
                closing = random.choice(DYNAMIC_CLOSINGS)
                return f"{greeting} {res_text}\n\n{closing}"
    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")

    return (
        f"{greeting} I noticed your warmth and support across Nostr and wanted to reach out with deep gratitude. "
        f"My family and I are enduring critical hardships in Gaza right now.\n\n"
        f"{random.choice(DYNAMIC_CLOSINGS)}"
    )

async def fetch_recent_zaps_ws():
    events = []
    seen_ids = set()
    for relay in GLOBAL_RELAYS:
        try:
            async with websockets.connect(relay, ping_interval=10, ping_timeout=10) as ws:
                req = json.dumps(["REQ", "zaps_sub", {"kinds": [9735], "limit": 60}])
                await ws.send(req)
                for _ in range(60):
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=2.5)
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

async def run_single_cycle():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets (NOSTR_NSEC or DEEPSEEK_API_KEY).")
        return

    try:
        keys = Keys.parse(NOSTR_SECRET)
        signer = NostrSigner.keys(keys)
    except Exception as e:
        print(f"Error parsing keys: {e}")
        return

    client = Client(signer)
    for r in GLOBAL_RELAYS:
        try:
            await client.add_relay(r)
        except Exception:
            pass

    await client.connect()
    print("Connected to Global Nostr Relays!")

    bot_pk = keys.public_key()
    bot_hex = bot_pk.to_hex().lower()

    print("Scanning Nostr network for active Zap supporters via WebSocket...")
    events = await fetch_recent_zaps_ws()
    print(f"Fetched {len(events)} zap events.")

    if not events:
        return

    dms_sent = 0
    seen_senders = set()

    for ev in events:
        if dms_sent >= MAX_DMS_PER_CYCLE:
            break

        sender_hex, sats = extract_zap_sender(ev)
        if not sender_hex:
            continue

        sender_hex = sender_hex.lower()
        if sender_hex == bot_hex or sender_hex in seen_senders:
            continue

        try:
            target_pk = PublicKey.parse(sender_hex)
        except Exception:
            continue

        seen_senders.add(sender_hex)

        user_name = await fetch_user_name_ws(sender_hex)
        dm_text = await asyncio.to_thread(generate_personalized_dm, sats, user_name)
        if not dm_text:
            continue

        try:
            # تشفير وبناء الرسالة المشفرة NIP-04 وإرسالها عبر العميل الموثق
            secret_key = keys.secret_key()
            encrypted_payload = nip04_encrypt(secret_key, target_pk, dm_text)
            
            p_tag = Tag.public_key(target_pk)
            builder = EventBuilder(Kind(4), encrypted_payload).tags([p_tag])
            
            await asyncio.wait_for(client.send_event_builder(builder), timeout=12)

            dms_sent += 1
            npub_short = target_pk.to_bech32()[:14] + "..."
            print(f"-> Sent DM #{dms_sent} to {user_name or 'Supporter'} ({npub_short}) [{sats or 'Active'} Sats]:")
            print(f"\"{dm_text}\"\n" + "-"*50)

            if dms_sent < MAX_DMS_PER_CYCLE:
                wait_secs = random.randint(12, 25)
                await asyncio.sleep(wait_secs)

        except Exception as send_err:
            print(f"Notice sending DM: {send_err}")

    print(f"Cycle finished: Sent {dms_sent} direct messages.")

async def main():
    print("Starting Nostr Zap Supporter Reach Engine...")
    max_cycles = 60
    current_cycle = 0

    while current_cycle < max_cycles:
        current_cycle += 1
        print(f"\n--- Starting Cycle {current_cycle}/{max_cycles} ---")
        try:
            await run_single_cycle()
        except Exception as e:
            print(f"Error in cycle execution: {e}")

        if current_cycle < max_cycles:
            print(f"Sleeping for {SLEEP_BETWEEN_CYCLES}s before next scan...")
            await asyncio.sleep(SLEEP_BETWEEN_CYCLES)

if __name__ == "__main__":
    asyncio.run(main())
