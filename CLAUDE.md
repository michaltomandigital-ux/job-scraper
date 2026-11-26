# Job-Finder

Minimální Python scraper pracovních nabídek. Cíl je JEDNODUCHOST: žádné zbytečné vrstvy, žádný registr pluginů, žádný konfigurační framework. Jediná konfigurace jsou dva datové soubory (`profese.json`, `blacklist.txt`), jediný stav na disku je `logs/` (audit a skip-list vyřazených) a zámek `.run.lock`. Nepřidávej funkce, které uživatel nezadal.

Stav, historie běhů, evidence změn slovníku a technické detaily portálů jsou v `DENIK.md` (tady je jen zadání a pravidla).

## Tvrdá pravidla (nikdy neporušit)
- Skript JEN hledá, parsuje a ukládá. NIKDY neodesílá reakce na inzeráty, nekontaktuje zaměstnavatele, nevyplňuje formuláře.
- POST jen na `api.notion.com` a `api.telegram.org` (vše přes `core._send`, který hlídá hostitele; PATCH jen `api.notion.com`). Na domény portálů nikdy POST.
- Respektovat robots.txt portálu (stáhne se jednou před hledáním, kontroluje se KAŽDÉ URL). Zakázané URL se přeskočí; když je zakázáno vše → `robots_disallowed`. Nikdy neobcházet (ani jiným User-Agentem).
- Pauza min. 2 s mezi požadavky na portál (`REQUEST_PAUSE`), poctivý User-Agent (jen ASCII).
- Žádná tajemství v kódu. `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` jen v `.env` (v `.gitignore`). `.env.example` zůstává BEZ hodnot. Tokeny nikdy nevypisuj do chatu ani do chyb (neloguj URL Telegramu).
- Limity `MAX_POSTED_DAYS=14` a `MAX_PAGES=5` nezvyšovat.
- Nula výsledků při HTTP 200 na všech portálových dotazech = `layout_changed` (upozornit). Jedna chyba portálu nesmí shodit ostatní.

## Zadání (co scraper dělá)
- Portály: jobs.cz, jenprace.cz. Lokality (`CITIES`, `PLACE_WORDS`): Hradec Králové, Pardubice, Rychnov nad Kněžnou, Kostelec nad Orlicí a obce kolem nich (od 24. 11. i Holice, Býšť). Výjimka: v NÁZVU „home office / práce z domova / remote“ projde místo jen na úrovni kraje nebo celé ČR (`HOME_OFFICE_RE`, `REGION_PLACE_RE`). Rozšíření okolí jen na zadání.
- Obory a profese: `profese.json`, 6 kategorií, pořadí = priorita (1 zákaznická podpora, 2 back office, 3 retail management, 4 prodej, 5 ostatní – sklad, montáž, 6 práce s AI). Každá má `search` (dotazy na portály), `title` (shoda v názvu), `signaly` (shoda v detailu u pozic bez shody v názvu; stačí `MIN_SIGNALS` různých; od 24. 11. VYPNUTO, seznamy leží v `signaly_vypnuto`, zapnutí = přejmenovat zpět na `signaly`, důvod viz DENIK.md) a volitelná `pravidla` (vyřadit pozici s daným názvem, když detail obsahuje výraz: fakturant při účetních pojmech, prodej a call centrum při provizi/IČO/odchozích hovorech). Změna oborů = úprava tohoto souboru, ne kódu.
- `py scrape.py` bez slov hledá podle `profese.json` (whitelist). Se zadanými slovy (`/hledej`, MCP) hledá jen je a whitelist se neuplatní; blacklist, lokalita a tvrdá pravidla platí vždy.
- Blacklist: `blacklist.txt`, výrazy v NÁZVU, vyřadí vždy a nic ho nepřebije (kuchař, recepční, řidič, finanční poradce, f2f…). Aktualizuje se ručně (viz Pravidla pro Claude), nikdy automaticky.
- Tvrdá vyřazení (název i detail), jakákoli zmínka: řidičské oprávnění jako POŽADAVEK (od 24. 11. zmínka s „výhodou / vítán / není podmínkou“ v téže větě nebo pod nadpisem „Výhodou:“ projde), VZV, noční směny, nepřetržitý provoz (jen ve spojení s provozem/směnami, holé „Hotline 24/7“ či „nonstop poradna“ je benefit), třísměnný provoz. Zápor na stejném řádku („bez nočních“) ruší jen noc, nepřetržitý a třísměnný provoz. Dvousměnný provoz je v pořádku.
- O každé nové nabídce rozhoduje JEDINÁ funkce `core.decide(job, fetch, profiles, blacklist)` → `Decision(keep, reason, category, score, source, note)`. Žádné další filtry jinde (nová pravidla patří do ní). Detail (GET) se stahuje jen když je potřeba, nejvýš jednou. Když detail nejde stáhnout: shoda v názvu se uloží se štítkem „ručně ověřit“ (`MANUAL_LABEL`, sloupec Poznámka), bez shody se nabídka dočasně neuloží.
- Vyřazené se do Notionu neukládají. Každé vyřazení má důvod a zapíše se do `logs/vyrazene.csv` (ostrý běh přidává; je zároveň skip-list na `MAX_POSTED_DAYS`, aby se detail nestahoval znovu); `--dry-run` přepíše `logs/vyrazene_dryrun.csv` a skip-list nečte. Po změně pravidel smazat `logs/vyrazene.csv`.
- Notion: povinné sloupce Název pozice, Firma, Místo výkonu práce, Pracovní portál, URL pozice, Stav („Nový“ musí existovat). Volitelné (přidat ručně, jinak se nezapíšou; shrnutí upozorní): Poznámka (text), Kategorie (select), Skóre (number), Nalezeno z (select: název/detail).
- Duplicity: podle URL a podle (název, firma, místo; `core._key`, portál v klíči NENÍ, takže stejná pozice na dvou portálech je jeden řádek). Zůstává NEJNOVĚJŠÍ řádek, starší se archivuje (koš Notionu); novější inzerát téže pozice (`core._supersedes`) se zapíše a starší řádek „Nový“ se archivuje až po úspěšném zápisu. Řádky s jiným stavem než „Nový“ se nikdy nearchivují ani nepřepisují. Klíč bez firmy i místa se nededuplikuje.
- Ostrý běh drží zámek `.run.lock` (`core._run_lock`), souběžný ostrý běh se odmítne; `--dry-run` zámek nepotřebuje.
- Zpětný úklid už zapsaných řádků v Notionu filtr neprovádí; jednorázový úklid (archivace do koše) jen po výslovném potvrzení uživatele, nikdy hromadně.

## Pravidla pro Claude (úprava slovníku)
- Na výslovný pokyn „přidej X na blacklist“ přidej slovo do `blacklist.txt`: malými písmeny, kmen slova, jedna položka na řádek, bez duplicit (ověř grepem; když je slovo v `profese.json` jako název oboru, upozorni na rozpor místo přidání).
- Přidej řádek s příkladem názvu do `test_decide_title_rules_without_profiles` v `test_core.py`, pusť `py -m pytest -q -k decide` a zapiš řádek do evidence v `DENIK.md` (datum, slovo, důvod).
- Změny `profese.json` (nový obor, synonymum, signál, pravidlo) a regexů jen na výslovné zadání.
- Návrhy: po ostrém běhu smíš z uložených nabídek navrhnout slova k blacklistu s příkladem názvu. Přidej je ALE AŽ PO potvrzení uživatele.

## Soubory
- `core.py` – veškerá logika: konstanty/selektory, parsery portálů, robots, `decide()` + načítání `profese.json`/`blacklist.txt`, Notion klient (raw httpx, zápis pod `data_source_id`), Telegram, `run()`.
- `profese.json`, `blacklist.txt` – data rozhodování (viz Zadání).
- `scrape.py` – CLI: `py scrape.py [slova...] [--dry-run] [--notify]`.
- `mcp_server.py` – tenký MCP server, jediný nástroj `run_scrape(keywords, dry_run)` (mcp 2.x, `MCPServer`; prázdná slova = profese).
- `telegram_bot.py` – long polling, reaguje JEN na `TELEGRAM_CHAT_ID`, jen `/hledej <slova>`; spouští pouze scraping a posílá jen shrnutí (žádný výčet nabídek).
- `test_core.py` + `tests/fixtures/` – offline testy (`py -m pytest`), vč. hlídání POST mimo povolené hosty a platnosti `profese.json`.
- `DENIK.md` – stav, historie běhů, evidence změn slovníku, technické poznámky k portálům.

## Spouštění
- Windows: `py` místo `python`; pro výpis s diakritikou `PYTHONIOENCODING=utf-8`. Závislosti: `py -m pip install -r requirements.txt` (testy: `requirements-dev.txt`; `mcp` je připnuté na 2.x).
- Ostrý běh zapisuje do Notionu a s `--notify` pošle shrnutí na Telegram (ostrý běh se spouští vždy s `--notify`; `scrape.py` neposílá seznam nabídek, jen shrnutí z `core.summary()`). Vždy nejdřív `--dry-run`. Příkaz `/hledej` z Telegramu je ostrý běh.
- Běh podle profilů je dlouhý (desítky dotazů × lokality × stránky × pauza 2 s + detaily), první běh nejdéle.
