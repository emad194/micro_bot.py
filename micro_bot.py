import os
import re
import json
import random
import asyncio
import requests
from datetime import timedelta
from nostr_sdk import (
    Client, NostrSigner, Keys, Filter, EventBuilder, Kind,
    PublicKey
)
import sys

sys.stdout.reconfigure(line_buffering=True)

NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_DMS_PER_CYCLE = 6         # عدد الرسائل الآمن في كل دورة
SLEEP_BETWEEN_CYCLES = 300     # 5 دقائق بين كل دورة

GLOBAL_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
    "wss://relay.primal.net",
    "wss://relay.snort.social",
    "wss://nostr.wine",
    "wss://purplepag.es",
    "wss://relay.current.fyi"
]

DYNAMIC_CLOSINGS = [
    "🕊️ You can check our daily survival story & updates pinned at the top of my profile if you feel led to read.",
    "🤍 We document our family's raw reality in Gaza on my pinned post if you'd like to take a look.",
    "✨ Our personal journey of resilience is pinned on my profile—any zap or share helps us survive.",
    "🌱 Quietly sharing our daily life amidst the ruins on my pinned note if you ever want to check.",
    "🍉 If you have a moment, our story and campaign are pinned at the top of my page. Warm regards."
]

def get_event_tags_list(event):
    try:
        raw_tags = event.tags() if callable(event.tags) else event.tags
        if hasattr(raw_tags, "to_vec"):
            return raw_tags.to_vec()
        return list(raw_tags)
    except Exception:
        return []

def parse_bolt11_sats(bolt11_invoice):
    """استخراج كمية الساتوشي بدقة من الفاتورة"""
    try:
        invoice_lower = str(bolt11_invoice).lower()
        if "lnbc" in invoice_lower:
            parts = invoice_lower.split("lnbc")[1]
            num_str = ""
            unit = ""
            for ch in parts:
                if ch.isdigit():
                    num_str += ch
                else:
                    unit = ch
                    break
            if num_str:
                val = int(num_str)
                if unit == 'm': return int(val * 100_000)
                elif unit == 'u': return int(val * 100)
                elif unit == 'n': return int(val / 10)
                elif unit == 'p': return int(val / 10_000)
                return val
    except Exception:
        pass
    return None

def extract_zap_sender(event):
    """استخراج المفتاح العام للداعم وقيمة الدعم"""
    sender_pubkey = None
    sats_amount = None

    try:
        tags = get_event_tags_list(event)
        for t in tags:
            vec = t.as_vec() if hasattr(t, "as_vec") else list(t)
            if len(vec) >= 2:
                tag_name = str(vec[0]).lower()
                tag_val = str(vec[1])

                if tag_name == 'bolt11':
                    sats_amount = parse_bolt11_sats(tag_val)
                elif tag_name == 'description':
                    try:
                        zap_req = json.loads(tag_val)
                        if "pubkey" in zap_req:
                            sender_pubkey = zap_req["pubkey"]
                    except Exception:
                        pass
    except Exception:
        pass

    return sender_pubkey, sats_amount

async def fetch_user_name(client, pubkey_hex):
    """جلب الاسم الأول للداعم من حسابه (Kind 0)"""
    try:
        f = Filter().author(PublicKey.parse(pubkey_hex)).kind(Kind(0)).limit(1)
        events = await client.fetch_events_from(GLOBAL_RELAYS, f, timedelta(seconds=4))
        ev_list = events.to_vec() if hasattr(events, "to_vec") else list(events)
        
        if ev_list:
            profile_content = ev_list[0].content() if callable(ev_list[0].content) else ev_list[0].content
            profile_data = json.loads(profile_content)
            name = profile_data.get("display_name") or profile_data.get("name")
            if name and len(name.strip()) > 0:
                clean_name = re.sub(r'[^\w\s]', '', name).strip()
                if clean_name:
                    return clean_name.split()[0]
    except Exception:
        pass
    return None

def generate_personalized_dm(sats_amount, user_name=None):
    """توليد رسالة شكر وتقدير عبر DeepSeek مع الاسم الأول"""
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

    # رسالة احتياطية
    return (
        f"{greeting} I noticed your warmth and support across Nostr and wanted to reach out with deep gratitude. "
        f"My family and I are enduring critical hardships in Gaza right now.\n\n"
        f"{random.choice(DYNAMIC_CLOSINGS)}"
    )

async def fetch_previously_messaged(client, bot_pk):
    """جلب سجل المحادثات السابقة لتفادي تكرار مراسلة أي شخص"""
    messaged = set()
    try:
        dm_filter = Filter().author(bot_pk).kind(Kind(4)).limit(300)
        events = await client.fetch_events_from(GLOBAL_RELAYS, dm_filter, timedelta(seconds=8))
        ev_list = events.to_vec() if hasattr(events, "to_vec") else list(events)
        for ev in ev_list:
            tags = get_event_tags_list(ev)
            for t in tags:
                vec = t.as_vec() if hasattr(t, "as_vec") else list(t)
                if len(vec) >= 2 and str(vec[0]).lower() == 'p':
                    messaged.add(str(vec[1]).lower())
    except Exception:
        pass
    return messaged

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

    print("Checking message history to prevent duplicates...")
    already_messaged = await fetch_previously_messaged(client, bot_pk)

    print("Scanning Nostr network for active Zap supporters...")
    zap_filter = Filter().kind(Kind(9735)).limit(150)
    
    try:
        events = await client.fetch_events_from(GLOBAL_RELAYS, zap_filter, timedelta(seconds=12))
        ev_list = events.to_vec() if hasattr(events, "to_vec") else list(events)
    except Exception as e:
        print(f"Error fetching zap events: {e}")
        return

    if not ev_list:
        print("No recent zap events found.")
        return

    dms_sent = 0
    seen_senders_this_cycle = set()

    for event in ev_list:
        if dms_sent >= MAX_DMS_PER_CYCLE:
            break

        sender_hex, sats = extract_zap_sender(event)
        if not sender_hex:
            continue

        sender_hex = sender_hex.lower()

        if sender_hex == bot_hex:
            continue
        if sender_hex in already_messaged or sender_hex in seen_senders_this_cycle:
            continue

        try:
            target_pk = PublicKey.parse(sender_hex)
        except Exception:
            continue

        seen_senders_this_cycle.add(sender_hex)

        # جلب اسم الداعم وتوليد الرسالة
        user_name = await fetch_user_name(client, sender_hex)
        dm_text = await asyncio.to_thread(generate_personalized_dm, sats, user_name)
        if not dm_text:
            continue

        try:
            # تشفير وإرسال الرسالة الخاصة (Kind 4 Encrypted DM)
            builder = EventBuilder.encrypted_direct_msg(keys, target_pk, dm_text)
            await asyncio.wait_for(client.send_event_builder(builder), timeout=12)

            dms_sent += 1
            already_messaged.add(sender_hex)

            npub_short = target_pk.to_bech32()[:14] + "..."
            print(f"-> Sent DM #{dms_sent} to {user_name or 'Supporter'} ({npub_short}) [{sats or 'Active'} Sats]:")
            print(f"\"{dm_text}\"\n" + "-"*50)

            if dms_sent < MAX_DMS_PER_CYCLE:
                wait_secs = random.randint(12, 25)
                await asyncio.sleep(wait_secs)

        except Exception as send_err:
            print(f"Notice sending DM to {sender_hex[:8]}: {send_err}")

    print(f"Cycle finished: Sent {dms_sent} direct messages to active supporters.")

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
