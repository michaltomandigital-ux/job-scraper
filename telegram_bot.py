"""Telegram bot (long polling). Reaguje JEN na TELEGRAM_CHAT_ID a jen na /hledej <slova...>.

Spouští pouze scraping (core.run), nic jiného.
"""
import html
import os
import time

import httpx
from dotenv import load_dotenv

import core


def handle(update: dict, allowed_chat: str) -> None:
    msg = update.get("message") or {}
    if str((msg.get("chat") or {}).get("id")) != allowed_chat:
        return  # cizí chaty se ignorují bez odpovědi
    parts = (msg.get("text") or "").split()
    if not parts or parts[0].split("@")[0] != "/hledej":
        return
    keywords = parts[1:]
    if not keywords:
        core.telegram_send("Použití: /hledej python analytik")
        return
    esc = lambda s: html.escape(s, quote=False)  # telegram_send posílá HTML
    core.telegram_send(esc(f"Hledám: {', '.join(keywords)} (chvíli to potrvá)"))
    try:
        res = core.run(keywords)
    except SystemExit as e:
        core.telegram_send(esc(f"Chyba: {e}"))
        return
    except Exception as e:  # jen typ chyby: text httpx chyb může obsahovat URL
        core.telegram_send(f"Hledání selhalo: {type(e).__name__}")
        return
    core.notify_done(res)


def main():
    load_dotenv()
    if not core.telegram_configured():
        raise SystemExit("Chybí TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID v .env")
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], str(os.environ["TELEGRAM_CHAT_ID"])
    offset = None
    with httpx.Client(timeout=60) as c:
        while True:
            try:
                params = {"timeout": 50, "allowed_updates": '["message"]'}
                if offset:
                    params["offset"] = offset
                r = c.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params)
                r.raise_for_status()
                for u in r.json().get("result", []):
                    offset = u["update_id"] + 1
                    handle(u, chat)
            except Exception as e:  # bot nesmí spadnout; jen typ, text chyby může obsahovat token v URL
                print(f"Chyba: {type(e).__name__}, zkusím znovu")
                time.sleep(5)


if __name__ == "__main__":
    main()
