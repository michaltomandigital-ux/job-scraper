# Job-Finder

Minimální Python scraper pracovních nabídek. Cíl je JEDNODUCHOST: žádné zbytečné vrstvy, žádná cache, žádný registr pluginů, žádný konfigurační systém. Nepřidávej funkce, které uživatel nezadal.

## Tvrdá pravidla (nikdy neporušit)
- Skript JEN hledá, parsuje a ukládá. NIKDY neodesílá reakce na inzeráty, nekontaktuje zaměstnavatele, nevyplňuje formuláře.
- POST jen na `api.notion.com` a `api.telegram.org` (vše přes `core._post`, který hlídá hostitele). Na domény portálů nikdy POST.
- Respektovat robots.txt portálu (kontrola před hledáním). Zakázáno → přeskočit a vypsat `robots_disallowed`. Nikdy neobcházet (ani jiným User-Agentem).
- Pauza min. 2 s mezi požadavky na portál (`REQUEST_PAUSE`), poctivý User-Agent (jen ASCII).
- Žádná tajemství v kódu. `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` jen v `.env` (v `.gitignore`). `.env.example` zůstává BEZ hodnot. Tokeny nikdy nevypisuj do chatu ani do chyb (neloguj URL Telegramu).
- Limity `MAX_POSTED_DAYS=14` a `MAX_PAGES=5` nezvyšovat.
- Nula výsledků při HTTP 200 na všech portálových dotazech = `layout_changed` (upozornit). Jedna chyba portálu nesmí shodit ostatní.

## Soubory
- `core.py` – veškerá logika: konstanty/selektory nahoře, parsery portálů, robots, Notion klient (raw httpx, `NOTION_VERSION`, zápis pod `data_source_id`), Telegram, `run()`. Lokality: `CITIES` (Hradec Králové, Pardubice, Rychnov nad Kněžnou, Kostelec nad Orlicí). Filtr oborů: `EXCLUDE_TITLE_WORDS` + `is_excluded()`.
- `scrape.py` – CLI: `py scrape.py "python" "analytik" [--dry-run] [--notify]`.
- `mcp_server.py` – tenký MCP server, jediný nástroj `run_scrape(keywords, dry_run)` (mcp 2.x, `MCPServer`).
- `telegram_bot.py` – long polling, reaguje JEN na `TELEGRAM_CHAT_ID`, jen `/hledej <slova>`; spouští pouze scraping.
- `test_core.py` + `tests/fixtures/` – offline testy (`py -m pytest`), vč. hlídání POST mimo povolené hosty.

## Stav (hotovo a ověřeno)
- jobs.cz: parser přes CSS (ve výpisu není JSON-LD), stránkování `&page=N`.
- jenprace.cz: filtr přes `?locations[0]=<ID>&subjects[0]=<slovo>`; ID měst jsou v `JENPRACE_CZ["city_ids"]` (Rychnov nad Kněžnou 732882, Kostelec nad Orlicí 732850, Hradec Králové 732549, Pardubice 733145; ID z `data-locations.json`). Za nadpisem „Podívejte se i na jiné nabídky“ jsou jen doporučené nabídky – parser je vynechává; „(0 inzerátů)“ = prázdný výsledek.
- Lokality hledání: Hradec Králové, Pardubice, Rychnov nad Kněžnou, Kostelec nad Orlicí (jen tyto, bez okolních měst – přidání jen na zadání).
- Filtr oborů (`EXCLUDE_TITLE_WORDS`): nabídka, jejíž NÁZEV obsahuje např. recepční, číšník, servírka, pokladní, uklízeč, brigádník, kuchař, řidič, se do Notionu neukládá; počítá se v „vyřazeno filtrem oborů“. Ostatní se uloží a filtr relevance je na uživateli v Notionu (přefiltrovat podle názvu).
- prace.cz: hledání podle slova je jen na `/hledat/`, které robots.txt zakazuje → vždy `robots_disallowed`. Neobcházet.
- Notion: sloupce Název pozice (title), Firma (text), Místo výkonu práce (text), Pracovní portál (select), **URL pozice** (url), Stav (status, hodnota „Nový“ musí existovat). Deduplikace podle URL, existující řádky se nikdy nepřepisují. Navíc: při každém běhu se archivují duplicity (stejný název+firma+místo; zůstává nejstarší řádek, archivace = koš Notionu, obnovitelné) a nová nabídka, jejíž pozice už v Notionu je, se nezapíše. Archivace jde přes `core._patch` (jen `api.notion.com`).
- Telegram: `--notify` a bot ověřeny; bot musí od uživatele dostat `/start`. Zprávy jdou jako HTML; první řádek s počtem je tučně, názvy a URL se escapují.
- Rate limit: HTTP 429 z Notionu/Telegramu se opakuje (`_post`, čeká podle `Retry-After`, max 5 pokusů).
- 404 na lokalitu portálu = jen tato lokalita je prázdná (jobs.cz nezná Kostelec nad Orlicí), ostatní se zpracují.
- Offline testy: 15 prošlo.

## Známé věci
- jobs.cz občas nahodile vrátí prázdnou stránku (`layout_changed`); existuje jeden retry, případně běh zopakovat.
- Výsledky jobs.cz i jenprace.cz jsou volné: jenprace.cz filtr podle slova propouští i nesouvisející obory (proto `EXCLUDE_TITLE_WORDS`); zbytek šumu se filtruje v Notionu ručně. Filtr podle názvu pozice nad rámec blacklistu nepřidávat bez dotazu.
- Notion API vrací občas 429 při větším počtu dotazů; řeší retry v `_post`.
- Běh 2026-11-06 (první): 289 nově zapsaných, 1195 už existovalo (dedup podle URL).
- Běh 2026-11-06 (s archivací duplicit): 5 nově zapsaných, 1448 už existovalo, 35 vyřazeno filtrem oborů, 17 duplicit archivováno do koše Notionu.
- Cross-portálové duplicity (stejná pozice na více portálech) se nededuplikují (dedup je podle URL).

## Poznámky pro spouštění
- Windows: `py` místo `python`; pro výpis s diakritikou `PYTHONIOENCODING=utf-8`.
- Ostrý běh zapisuje do Notionu – nejdřív `--dry-run`. Příkaz `/hledej` z Telegramu je ostrý běh.
- Závislosti: `py -m pip install -r requirements.txt`.
