"""Ruční CLI: py scrape.py "python" "analytik" [--dry-run] [--notify]"""
import argparse

import core


def main():
    ap = argparse.ArgumentParser(description="Hledá pracovní nabídky a ukládá je do Notionu.")
    ap.add_argument("keywords", nargs="+", help="klíčová slova (hledá se každé zvlášť)")
    ap.add_argument("--dry-run", action="store_true", help="nic nezapisovat, jen vypsat")
    ap.add_argument("--notify", action="store_true", help="poslat nové pozice do Telegramu")
    args = ap.parse_args()

    res = core.run(args.keywords, dry_run=args.dry_run)
    for name, state in res["portals"].items():
        print(f"[{name}] {state}")
    verb = "by se uložilo" if args.dry_run else "uloženo"
    print(f"\nNových nabídek ({verb}): {len(res['new'])}, už existuje: {res['skipped_existing']}, "
          f"vyřazeno filtrem oborů: {res['excluded']}, duplicit {'k archivaci' if args.dry_run else 'archivováno'}: "
          f"{res['duplicates_archived']}")
    for j in res["new"]:
        print(f"- {j.title} | {j.company} | {j.place} | {j.url}")

    if args.notify:
        if args.dry_run:
            print("Telegram: --dry-run, nic se neposílá")
        elif not core.telegram_configured():
            print("Telegram: chybí TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID v .env")
        elif core.notify_telegram(res["new"]):
            print("Telegram: odesláno")
        else:
            print("Telegram: žádné nové pozice, nic se neposílá")


if __name__ == "__main__":
    main()
