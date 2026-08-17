import os
import re
import random
import time
import requests

# ==================== الإعدادات والمفاتيح ====================
MICRO_TOKEN = os.getenv("MICRO_TOKEN", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MAX_REPLIES_PER_RUN = 10

DYNAMIC_CLOSINGS = [
    "🕊️ Documenting our family's daily reality in Gaza on my profile link if you'd like to check it out.",
    "✨ Sharing raw updates & our personal story from Gaza in my bio link.",
    "🍉 If you have a moment, our story and daily struggle in Gaza are linked on my profile.",
    "🤍 Holding onto hope amidst the ruins—our journey is shared on my page if you feel like reading.",
    "🕊️ I share honest dispatches of our life in Gaza on my profile. Appreciate kind eyes on it.",
    "🌱 Documenting what survival looks like for our family here in my profile link if you wish to see.",
    "✨ Sharing our lived reality from Gaza on my page. Wishing you peace.",
    "🕊️ Pinned our personal story & updates on my profile if you'd like to connect with our reality in Gaza.",
    "🍉 Sharing glimpses of our days and hope from Gaza in my bio link.",
    "🤍 Our story and family updates from northern Gaza are linked on my profile if you care to read."
]

def strip_html(text):
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text).strip()

def is_clean_english(text):
    if not text: return False
    cjk_pattern = re.compile(r'[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uffef\u4e00-\u9faf\uac00-\ud7af]')
    if cjk_pattern.search(text): return False
    latin_chars = len(re.findall(r'[a-zA-Z0-9\s.,!?\'"\-]', text))
    total_chars = len(text)
    return total_chars > 0 and (latin_chars / total_chars) >= 0.65

def contains_video(text):
    video_domains = ["youtube.com", "youtu.be", "vimeo.com", "tiktok.com", ".mp4", ".mov"]
    return any(domain in text.lower() for domain in video_domains)

def is_spam(text):
    text_lower = text.lower()
    if len(re.findall(r'https?://\S+', text)) >= 2: return True
    return any(kw in text_lower for kw in ["solana", "airdrop", "presale", "crypto", "giveaway"])

def generate_ai_reply(prompt_text):
    if not prompt_text or len(prompt_text) < 10:
        return None

    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    system_instruction = (
        "You are Imad, a genuine human being living through the hardships in Gaza, replying authentically on a microblogging platform.\n\n"
        "TASK:\n"
        "Write exactly ONE authentic, conversational, and direct sentence reacting specifically to what the author posted.\n\n"
        "RULES:\n"
        "1. Write ONLY the 1-sentence reaction. Do NOT add any closing note or link.\n"
        "2. Do NOT use cliché chatbot openings like 'I agree', 'Great perspective', 'Thanks for sharing'.\n"
        "3. Sound like a real, thoughtful human on social media.\n"
        "4. If the post is non-English, pure spam, gibberish, or code, respond ONLY with: SKIP"
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Post to reply to: '{prompt_text}'"}
        ],
        "temperature": 0.85
    }
    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            res_text = response.json()["choices"][0]["message"]["content"].strip().replace('"', '')
            if "SKIP" in res_text or len(res_text) < 5: return None
            if not is_clean_english(res_text): return None
            
            chosen_closing = random.choice(DYNAMIC_CLOSINGS)
            return f"{res_text}\n\n{chosen_closing}"
    except Exception as e:
        print(f"[!] Error calling DeepSeek API: {e}")
    return None

def run():
    if not MICRO_TOKEN or not DEEPSEEK_API_KEY:
        print("[!] Missing secrets in GitHub.")
        return

    print("[*] Fetching Discover timeline from Micro.blog...")
    headers = {"Authorization": f"Bearer {MICRO_TOKEN}"}
    
    try:
        res = requests.get("https://micro.blog/posts/discover", headers=headers, timeout=10)
        res.raise_for_status()
        posts = res.json().get("items", [])
    except Exception as e:
        print(f"[!] Error fetching timeline: {e}")
        return

    replies_count = 0
    
    for post in posts:
        if replies_count >= MAX_REPLIES_PER_RUN:
            break
        
        post_id = post.get("id")
        author_info = post.get("author", {})
        author_username = author_info.get("_microblog", {}).get("username", "")
        
        if not author_username:
            continue
        
        html_content = post.get("content_html", "")
        clean_text = strip_html(html_content)

        if len(clean_text) < 15: continue
        if not is_clean_english(clean_content := clean_text): continue
        if contains_video(clean_text) or is_spam(clean_text): continue

        print(f"[*] Analyzing post by @{author_username}...")
        reply_text = generate_ai_reply(clean_text)
        
        if reply_text:
            print(f"[*] Sending reply to @{author_username}...")
            try:
                reply_payload = {"id": post_id, "text": reply_text}
                reply_res = requests.post("https://micro.blog/posts/reply", data=reply_payload, headers=headers, timeout=10)
                
                if reply_res.status_code == 200:
                    replies_count += 1
                    print(f"    [✓] Reply #{replies_count} sent successfully:\n{reply_text}\n")
                    
                    sleep_time = random.randint(15, 25)
                    time.sleep(sleep_time)
                else:
                    print(f"    [!] Status {reply_res.status_code}: {reply_res.text}")
            except Exception as e:
                print(f"    [!] Error sending: {e}")

    print(f"[+] Finished run. Total replies sent: {replies_count}")

if __name__ == "__main__":
    run()
