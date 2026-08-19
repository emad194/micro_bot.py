import os
import re
import json
import random
import asyncio
import requests
from datetime import timedelta
from nostr_sdk import (
    Client, NostrSigner, Keys, Filter, EventBuilder, Tag, Kind,
    PublicKey
)
import sys

sys.stdout.reconfigure(line_buffering=True)

NOSTR_SECRET = os.getenv("NOSTR_NSEC", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_REPLIES = 8
SLEEP_BETWEEN_CYCLES = 240

# أقوى ريليهات نوستر المركزية للبث والاستماع
GLOBAL_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://relay.nostr.band",
    "wss://relay.snort.social",
    "wss://purplepag.es",
    "wss://nostr.wine",
    "wss://relay.current.fyi",
    "wss://nostr.bitcoiner.social",
    "wss://relay.plebstr.com",
    "wss://eden.nostr.land"
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

def extract_zap_data(event_tags):
    sender_pubkey = None
    target_event_id = None
    sats_amount = None

    for tag in event_tags:
        vec = tag.as_vec() if hasattr(tag, "as_vec") else list(tag)
        if len(vec) >= 2:
            key = str(vec[0]).lower()
            val = str(vec[1])

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

async def fetch_user_meta(client, author_pk):
    name = None
    last_post_id = None
    try:
        f = Filter().author(author_pk).kinds([Kind(0), Kind(1)]).limit(4)
        events = await client.fetch_events([f], timedelta(seconds=4))
        ev_list = events.to_vec() if hasattr(events, "to_vec") else list(events)
        for ev in ev_list:
            ev_kind = ev.kind().as_u64() if hasattr(ev.kind(), "as_u64") else int(ev.kind())
            if ev_kind == 0 and not name:
                content = json.loads(ev.content() if callable(ev.content) else ev.content or "{}")
                name_val = content.get("display_name") or content.get("name")
                if name_val:
                    clean = re.sub(r'[^\w\s]', '', name_val).strip()
                    if clean:
                        first_word = clean.split()[0]
                        if is_valid_human_name(first_word):
                            name = first_word.capitalize()
            elif ev_kind == 1 and not last_post_id:
                ev_id_obj = ev.id() if callable(ev.id) else ev.id
                last_post_id = ev_id_obj.to_hex() if hasattr(ev_id_obj, "to_hex") else str(ev_id_obj)
    except Exception:
        pass
    return name, last_post_id

def generate_personalized_reply(sats_amount, user_name=None):
    greeting = f"Hi {user_name}," if user_name else "Hi,"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    
    amount_hint = f"They are an active supporter who zaps community creators on Nostr (around {sats_amount} sats)." if sats_amount else "They are active in supporting community creators with zaps on Nostr."
    
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
        "temperature": 0.85
    }

    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=12)
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

async def run_single_cycle():
    if not NOSTR_SECRET or not DEEPSEEK_API_KEY:
        print("Error: Missing secrets in GitHub.")
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

    # قراءة سجل الردود الحية من الشبكة لمنع أي تعارض مع أي سكربت آخر
    already_replied_events = set()
    already_replied_authors = set()

    try:
        history_filter = Filter().author(bot_pk).kind(Kind(1)).limit(300)
        history_obj = await client.fetch_events([history_filter], timedelta(seconds=6))
        history_list = history_obj.to_vec() if hasattr(history_obj, "to_vec") else list(history_obj)
        for h_event in history_list:
            raw_tags = h_event.tags() if callable(h_event.tags) else h_event.tags
            tag_list = raw_tags.to_vec() if hasattr(raw_tags, "to_vec") else list(raw_tags)
            for t in tag_list:
                vec = t.as_vec() if hasattr(t, "as_vec") else list(t)
                if len(vec) >= 2:
                    t_type, t_val = str(vec[0]).lower(), str(vec[1]).lower()
                    if t_type == 'e': already_replied_events.add(t_val)
                    elif t_type == 'p': already_replied_authors.add(t_val)
    except Exception as e:
        print(f"Notice fetching history: {e}")

    # البحث عن آخر أحداث الـ Zaps (Kind 9735) في الشبكة
    print("Scanning Nostr network for Zap events...")
    zap_filter = Filter().kind(Kind(9735)).limit(100)
    try:
        zap_events = await client.fetch_events([zap_filter], timedelta(seconds=8))
        zap_list = zap_events.to_vec() if hasattr(zap_events, "to_vec") else list(zap_events)
    except Exception as e:
        print(f"Error fetching zaps: {e}")
        return

    print(f"Fetched {len(zap_list)} zap receipts.")
    if not zap_list:
        return

    replies_count = 0
    session_senders = set()

    for z_event in zap_list:
        if replies_count >= MAX_REPLIES:
            break

        raw_tags = z_event.tags() if callable(z_event.tags) else z_event.tags
        tags_list = raw_tags.to_vec() if hasattr(raw_tags, "to_vec") else list(raw_tags)
        
        sender_hex, target_event_id, sats = extract_zap_data(tags_list)
        if not sender_hex:
            continue

        sender_hex = sender_hex.lower()
        if sender_hex == bot_hex:
            continue
        if sender_hex in already_replied_authors or sender_hex in session_senders:
            continue

        try:
            target_pk = PublicKey.parse(sender_hex)
        except Exception:
            continue

        session_senders.add(sender_hex)

        user_name, last_post_id = await fetch_user_meta(client, target_pk)
        event_to_reply = target_event_id or last_post_id
        if not event_to_reply or event_to_reply in already_replied_events:
            continue

        reply_text = await asyncio.to_thread(generate_personalized_reply, sats, user_name)
        if not reply_text:
            continue

        try:
            # وسم NIP-10 المعتمد
            t_root = Tag.parse(["e", event_to_reply, "", "root"])
            t_reply = Tag.parse(["e", event_to_reply, "", "reply"])
            t_pubkey = Tag.parse(["p", sender_hex])
            builder = EventBuilder(Kind(1), reply_text, [t_root, t_reply, t_pubkey])

            await client.send_event_builder(builder)

            replies_count += 1
            already_replied_authors.add(sender_hex)
            already_replied_events.add(event_to_reply)

            print(f"-> Successfully replied #{replies_count} to {user_name or 'Supporter'} [{sats or 'Active'} Sats]:")
            print(f"\"{reply_text}\"\n" + "-"*50)

            if replies_count < MAX_REPLIES:
                await asyncio.sleep(random.randint(6, 12))

        except Exception as send_err:
            print(f"Notice sending reply: {send_err}")

    print(f"Cycle finished: Sent {replies_count} targeted replies.")

async def main():
    print("Starting Global Nostr Zap Supporter Bot...")
    cycle = 0

    while True:
        cycle += 1
        print(f"\n--- Starting Cycle #{cycle} ---")
        try:
            await run_single_cycle()
        except Exception as e:
            print(f"Error in cycle execution: {e}")

        print(f"Waiting 4 minutes ({SLEEP_BETWEEN_CYCLES}s) before next scan...")
        await asyncio.sleep(SLEEP_BETWEEN_CYCLES)

if __name__ == "__main__":
    asyncio.run(main())
