"""Ruční CLI: py scrape.py "python" "analytik" [--dry-run] [--notify]"""
import argparse

import httpx

import core


def main():
    ap = argparse.ArgumentParser(description="Hledá pracovní nabídky a ukládá je do Notionu.")
    ap.add_argument("keywords", nargs="+", help="klíčová slova (hledá se každé zvlášť)")
    ap.add_argument("--dry-run", action="store_true", help="nic nezapisovat, jen vypsat")
    ap.add_argument("--notify", action="store_true", help="po dokončení poslat do Telegramu oznámení se shrnutím")
    args = ap.parse_args()

    res = core.run(args.keywords, dry_run=args.dry_run)
    for name, state in res["portals"].items():
        print(f"[{name}] {state}")
    verb = "by se uložilo" if args.dry_run else "uloženo"
    print(f"\nNových nabídek ({verb}): {len(res['new'])}, už existuje: {res['skipped_existing']}, "
          f"vyřazeno (obor/lokalita): {res['excluded']}, vyřazeno (řidičský průkaz): {res['excluded_licence']}, vyřazeno (noční směny v detailu): {res['excluded_night']}, vyřazeno (VZV v detailu): {res['excluded_vzv']}, "
          f"detail neověřen: {res['licence_unchecked']}, duplicit {'k archivaci' if args.dry_run else 'archivováno'}: "
          f"{res['duplicates_archived']}, chyby zápisu: {res['write_errors']}")
    for j in res["new"]:
        print(f"- {j.title} | {j.company} | {j.place} | {j.url}")

    if args.notify:
        if args.dry_run:
            print("Telegram: --dry-run, nic se neposílá")
        elif not core.telegram_configured():
            print("Telegram: chybí TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID v .env")
        else:
            try:
                core.notify_done(res)
                print("Telegram: oznámení o dokončení odesláno")
            except (RuntimeError, httpx.HTTPError) as e:  # data v Notionu už jsou; text httpx chyby může nést URL s tokenem
                print(f"Telegram: oznámení selhalo ({e if isinstance(e, RuntimeError) else type(e).__name__})")


if __name__ == "__main__":
    main()
