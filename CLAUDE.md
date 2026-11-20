# Job-Finder

Minimální Python scraper pracovních nabídek. Cíl je JEDNODUCHOST: žádné zbytečné vrstvy, žádná cache, žádný registr pluginů, žádný konfigurační systém. Nepřidávej funkce, které uživatel nezadal.

## Tvrdá pravidla (nikdy neporušit)
- Skript JEN hledá, parsuje a ukládá. NIKDY neodesílá reakce na inzeráty, nekontaktuje zaměstnavatele, nevyplňuje formuláře.
- POST jen na `api.notion.com` a `api.telegram.org` (vše přes `core._send`, který hlídá hostitele; PATCH jen `api.notion.com`). Na domény portálů nikdy POST.
- Respektovat robots.txt portálu (stáhne se jednou před hledáním, kontroluje se KAŽDÉ URL). Zakázané URL se přeskočí; když je zakázáno vše → `robots_disallowed`. Nikdy neobcházet (ani jiným User-Agentem).
- Pauza min. 2 s mezi požadavky na portál (`REQUEST_PAUSE`), poctivý User-Agent (jen ASCII).
- Žádná tajemství v kódu. `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` jen v `.env` (v `.gitignore`). `.env.example` zůstává BEZ hodnot. Tokeny nikdy nevypisuj do chatu ani do chyb (neloguj URL Telegramu).
- Limity `MAX_POSTED_DAYS=14` a `MAX_PAGES=5` nezvyšovat.
- Nula výsledků při HTTP 200 na všech portálových dotazech = `layout_changed` (upozornit). Jedna chyba portálu nesmí shodit ostatní.

## Soubory
- `core.py` – veškerá logika: konstanty/selektory nahoře, parsery portálů, robots, Notion klient (raw httpx, `NOTION_VERSION`, zápis pod `data_source_id`), Telegram, `run()`. Lokality: `CITIES` (Hradec Králové, Pardubice, Rychnov nad Kněžnou, Kostelec nad Orlicí). Filtr oborů: `BLACKLIST` / `BLACKLIST_HARD` / `WHITELIST` + `is_excluded()` (viz „Slovník filtru“).
- `scrape.py` – CLI: `py scrape.py "python" "analytik" [--dry-run] [--notify]`.
- `mcp_server.py` – tenký MCP server, jediný nástroj `run_scrape(keywords, dry_run)` (mcp 2.x, `MCPServer`).
- `telegram_bot.py` – long polling, reaguje JEN na `TELEGRAM_CHAT_ID`, jen `/hledej <slova>`; spouští pouze scraping.
- `test_core.py` + `tests/fixtures/` – offline testy (`py -m pytest`), vč. hlídání POST mimo povolené hosty.

## Stav (hotovo a ověřeno)
- jobs.cz: parser přes CSS (ve výpisu není JSON-LD; parsery JSON-LD nečtou, jen CSS), stránkování `&page=N`.
- jenprace.cz: filtr přes `?locations[0]=<ID>&subjects[0]=<slovo>`; ID měst jsou v `JENPRACE_CZ["city_ids"]` (Rychnov nad Kněžnou 732882, Kostelec nad Orlicí 732850, Hradec Králové 732549, Pardubice 733145; ID z `data-locations.json`). Za nadpisem „Podívejte se i na jiné nabídky“ jsou jen doporučené nabídky – parser je vynechává; „(0 inzerátů)“ = prázdný výsledek.
- Lokality hledání (`CITIES`): Hradec Králové, Pardubice, Rychnov nad Kněžnou, Kostelec nad Orlicí. Portály k nim vracejí i široké okolí (Kolín, Havlíčkův Brod…), proto se místo výkonu práce filtruje: `PLACE_WORDS` + `place_ok()` – uloží se jen nabídka, jejíž místo obsahuje jedno z těchto měst/obcí (čtyři města + obce kolem Rychnova a Kostelce: Solnice, Vamberk, Častolovice, Doudleby n. Orl., Albrechtice n. Orl., Týniště n. Orl., Borohrádek, Opočno, Dobruška). Chrudim, Lázně Bohdaneč, kraje a „Celá ČR“ neprojdou; prázdné místo projde. Rozšíření okolí jen na zadání, upravit `PLACE_WORDS`.
- Filtr oborů (`BLACKLIST`): nabídka, jejíž NÁZEV obsahuje např. recepční, číšník, servírka, pokladní, uklízeč, brigádník, kuchař, řidič, lékař, sestra, sociální pracovník, ošetřovatel, zdravotnick…, lékař/doktor/MUDr, sociální služby a péče (pečovatel, pečující, asistent péče, „domov pro…“), se do Notionu neukládá. Skladník se neblokuje (uživatel to výslovně chtěl): název se „skladn“ (`WHITELIST`) seznam nevyřadí, např. „Skladník – řidič“. Noční směny (`NIGHT_RE`, jen začátek slova „nočn“, takže „vánoční“ neprojde jako noční) vyřadí nabídku vždy, i skladníka: v názvu, a také v detailu (`has_night_shift`, stejný GET jako u řidičáku, `fetch_detail_text`), pokud na STEJNÉM řádku není zápor (`NIGHT_NEG_RE`: bez/není/nejsou/žádn…); počítá se `excluded_night`. VZV (`VZV_RE`: „VZV“ nebo „vysokozdvižn…“) vyřadí nabídku VŽDY, v názvu i v detailu, ať je podmínkou, výhodou, nebo „bez nutnosti VZV“ (přání uživatele); i skladníka. V detailu se počítá `excluded_vzv`. `fetch_detail_text` před kontrolou odstraní `header`, `nav`, `footer` – patička jenprace.cz má „Top profese“ s „Obsluha vysokozdvižných vozíků“ a „Řidič / Řidička“, jinak by se vyřadila každá nabídka (ověřeno živě 2026-11-11).
- Řidičský průkaz: u nových nabídek (po filtrech oboru/lokality a po dedupu) se načte detail (GET, `REQUEST_PAUSE`, po kontrole robots.txt) a nabídka se vyřadí, když ŘP je podmínka (`requires_driving_licence`: řádek s „řidičský průkaz/oprávnění“ bez „výhodou/vítán/není podmínkou/plus“). Odkaz „Řidič / Řidička“ v menu portálu se nepočítá. Nešlo-li detail ověřit, nabídka se uloží a počítá se „detail neověřen“.
- Úklid už zapsaných řádků filtr zpětně neprovádí; jednorázový úklid (archivace do koše Notionu) jen po výslovném potvrzení uživatele, nikdy hromadně bez něj.
- Notion: sloupce Název pozice (title), Firma (text), Místo výkonu práce (text), Pracovní portál (select), **URL pozice** (url), Stav (status, hodnota „Nový“ musí existovat). Deduplikace podle URL, existující řádky se nikdy nepřepisují. Navíc: při každém běhu se archivují duplicity (stejný název+firma+místo, na portálu nezáleží; zůstává NEJNOVĚJŠÍ řádek, starší se archivují = koš Notionu, obnovitelné). Nově nalezený inzerát téže pozice (jiné URL), jehož datum zveřejnění je pozdější než zápis stávajícího řádku „Nový“ (`core._supersedes`), se zapíše a starší řádek se archivuje až po úspěšném zápisu; jinak se nezapíše (rozpracované řádky se nepřepisují, stejné datum nepřepisuje, aby se pozice nepřepisovala při každém běhu). Archivace jde přes `core._send(..., "PATCH", ...)` (jen `api.notion.com`), výběr duplicit je `split_duplicates()` (řádky s jiným stavem než „Nový“ se nikdy nearchivují).
- Telegram: `--notify` a bot ověřeny; bot musí od uživatele dostat `/start`. Zprávy jdou jako HTML; první řádek s počtem je tučně, názvy a URL se escapují.
- Standardní logika každého ostrého běhu: nové nabídky se ukládají do Notionu a po dokončení scrapingu přijde do Telegramu oznámení „Scraping dokončen“ (počet nově uložených, už existujících, vyřazených filtry, chyby zápisu, stav portálů; posílá se i při 0 nových). Ostrý běh se proto vždy spouští s `--notify` (`scrape.py` neposílá seznam nabídek, jen toto shrnutí) – uživatel to nemusí zadávat znovu. `--notify` s `--dry-run` nic neposílá. Text shrnutí skládá jen `core.summary()` (CLI, MCP, bot); do Telegramu jde escapovaný přes `core.notify_done()`. Do Telegramu nechodí seznam nabídek, jen toto shrnutí (i z bota `/hledej`). Selhání Telegramu nesmí shodit CLI (data v Notionu už jsou).
- Rate limit: HTTP 429 z Notionu/Telegramu se opakuje (`_send`, čeká podle `Retry-After`, max 5 pokusů).
- 404 na lokalitu portálu = jen tato lokalita je prázdná (jobs.cz nezná Kostelec nad Orlicí), ostatní se zpracují.
- Tituly „prověřit“ (`REVIEW_TITLE_RE`: telesales, account manager, obchodní zástupce, sales…): z detailu se spočítají různé signály (`SALES_SIGNALS`, `SERVICE_SIGNALS`) v `classify_description()`. Provize/IČO/odchozí hovory → vyřadit (`excluded_sales`); víc prodejních → vyřadit; víc servisních → ponechat; remíza/žádné signály/detail nedostupný → ponechat se štítkem „ručně ověřit“ (`MANUAL_LABEL`, do volitelného sloupce „Poznámka“ v Notionu, pokud existuje; počet `manual_check` je v shrnutí). Natvrdo v názvu vyřazuje `BLACKLIST_HARD` (finanční/pojišťovací poradce, f2f, door to door).
- Relevance oboru (`TARGET`, `is_relevant()`, 2026-11-20): nabídka musí patřit do cílových oborů (zákaznická podpora, back office, retail management, finance/pojištění, veřejná správa, prodej, montáž/výroba). Nabídka MUSÍ mít zásah vzoru v názvu (detail ho jen potvrdí: ≥2 různé vzory, `TARGET_MIN_DETAIL_TITLE`; detail nedostupný nebo kratší než `TARGET_MIN_TEXT` = potvrzení se přeskočí) NEBO státního zaměstnavatele (`STATE_RE` na firmě: úřad, ministerstvo, město, kraj, pošta…). Detail sám nabídku nezachrání (oprava 2026-11-21: dřív prošlo cokoli s krátkým/chybějícím detailem nebo 3 obecnými zásahy, např. montér plynovodů, autoklempíř). Počítá se `excluded_irrelevant`. Kmeny jsou odhad; ladit podle běhů.
- Offline testy: viz `py -m pytest` (po auditu přibyly testy stránkování, 429, `load_rows`, zámku, duplicit s prázdným klíčem).
- Zámek `.run.lock` (`core._run_lock`): ostrý běh (CLI/bot/MCP) nesmí běžet souběžně s jiným ostrým během; zámek starší než hodina se ignoruje; `--dry-run` zámek nepotřebuje.
- Stránkování nekončí na „stránka bez nových nabídek“ (překryv měst), jen na prázdné stránce, starých nabídkách nebo `MAX_PAGES`.
- Dedup podle (název, firma, místo; `core._key`) se přeskočí, když je firma i místo prázdné (rozbitý selektor).
- Řidičák: „výhodou“ na DALŠÍM řádku platí jen když je celý řádek jen toto slovo (`LICENCE_NEXT_RE`), ne nadpis „Výhody“.

## Slovník filtru (blacklist / whitelist)
Slova žijí na JEDNOM místě: seznamy v `core.py` (u konstant, s komentářem). Zde je jen pravidlo a evidence změn.

| Seznam | Co dělá | Přebíjí ho |
|---|---|---|
| `BLACKLIST_HARD` | vyřadí vždy podle názvu (finanční poradce, f2f…) | nic |
| noc (`NIGHT_RE`), VZV (`VZV_RE`) | vyřadí v názvu i detailu | nic (ani whitelist) |
| `WHITELIST` | název se slovem se `BLACKLIST` nevyřadí (např. „skladn“ → „Skladník – řidič“) | `BLACKLIST_HARD`, noc, VZV |
| `BLACKLIST` | vyřadí podle názvu (recepční, kuchař, řidič, zdravotnictví…) | `WHITELIST` |
Dál v detailu: řidičský průkaz jako podmínka a prodejní signály u „prověřit“ titulů (viz Stav).

**Pravidla pro Claude**
- Na výslovný pokyn „přidej X na blacklist / whitelist“ (u blacklistu „natvrdo“ = `BLACKLIST_HARD`, jinak `BLACKLIST`) slovo přidej do příslušného seznamu v `core.py`: malými písmeny, kmen slova, jedna položka na slovo, bez duplicit (ověř grepem, a když je slovo už v jiném seznamu, upozorni na rozpor místo přidání).
- Přidej řádek s příkladem názvu do `test_filter_order_excluded_blacklist_whitelist` v `test_core.py` a pusť jen `py -m pytest -q -k excluded`, ne celou sadu.
- Zapiš řádek do evidence níže (datum, seznam, slovo, důvod). Jiné zásahy do filtru (nová pravidla, regexy) dál jen na výslovné zadání.
- Návrhy: po ostrém běhu smíš navrhnout slova k blacklistu/whitelistu z uložených nabídek, které k hledání nesedí, s příkladem názvu. Přidej je ALE AŽ PO potvrzení uživatele.
- Uklízet už zapsané řádky v Notionu se nesmí (viz Stav).

**Evidence změn slovníku** (nejnovější nahoře; slova jsou v `core.py`)
- 2026-11-20: `BLACKLIST` +zámečník, svářeč, autoklempíř, instalatér, topenář, montér, elektromontáž, plynovod, realit, makléř, investiční specialist, systémový inženýr, informačních systémů (šum z ostrého běhu, např. „Montér/opravář plynovodů“, „Ženy a reality“; na pokyn uživatele). Už zapsané řádky v Notionu zůstávají.
- 2026-11-20: `prodavač` odebráno z `BLACKLIST_HARD` (přání uživatele; whitelist by HARD nepřebil). `pokladní`, `recepční` už v `BLACKLIST` jsou, beze změny.
- 2026-11-19: přejmenování seznamů na `BLACKLIST` / `BLACKLIST_HARD` / `WHITELIST`, obsah beze změny; pořadí zdokumentováno v tabulce výše.

## Známé věci
- jobs.cz občas nahodile vrátí prázdnou stránku (`layout_changed`); existuje jeden retry, případně běh zopakovat.
- Výsledky jobs.cz i jenprace.cz jsou volné: jenprace.cz filtr podle slova propouští i nesouvisející obory (proto `BLACKLIST`); zbytek šumu se filtruje v Notionu ručně. Slova do filtru se přidávají jen podle pravidel v části „Slovník filtru“.
- Notion API vrací občas 429 při větším počtu dotazů; řeší retry v `_send`.
- Běh 2026-11-06 (první): 289 nově zapsaných, 1195 už existovalo (dedup podle URL).
- Běh 2026-11-06 (s archivací duplicit): 5 nově zapsaných, 1448 už existovalo, 35 vyřazeno filtrem oborů, 17 duplicit archivováno do koše Notionu.
- Cross-portálové duplicity (stejná pozice na více portálech) se deduplikují (přání uživatele): portál NENÍ v klíči duplicity, takže zůstane jen jeden řádek (nejnovější, starší se archivuje).

## Poznámky pro spouštění
- Windows: `py` místo `python`; pro výpis s diakritikou `PYTHONIOENCODING=utf-8`.
- Ostrý běh zapisuje do Notionu a s `--notify` pošle oznámení o dokončení na Telegram – nejdřív `--dry-run`. Příkaz `/hledej` z Telegramu je ostrý běh.
- Závislosti: `py -m pip install -r requirements.txt` (testy: `requirements-dev.txt`; `mcp` je připnuté na 2.x).
