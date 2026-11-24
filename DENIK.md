# Deník a technické poznámky (mimo CLAUDE.md)

Zadání a pravidla jsou v `CLAUDE.md`. Tady je historie a to, co se ověřilo na živých portálech.

## 2026-11-24: profese, decide(), vyřazení podle zadání
- Whitelist oborů a signály z detailu v `profese.json`; blacklist v `blacklist.txt` (sloučené dřívější `BLACKLIST` a `BLACKLIST_HARD`); jedna rozhodovací funkce `decide()`; důvody vyřazení v `logs/vyrazene*.csv`.
- Nahradilo dřívější filtr: `BLACKLIST`/`BLACKLIST_HARD`/`WHITELIST`, `is_excluded()`, relevanci `TARGET`/`is_relevant()`/`STATE_RE` a klasifikaci prodejních × servisních signálů (`classify_description()`, `REVIEW_TITLE_RE`).
- Převzato jako data: pravidlo „provize / IČO / odchozí hovory vyřadí“ (prodej, call centrum) v `profese.json`; slova z BLACKLIST/BLACKLIST_HARD.
- Změny chování: řidičské oprávnění vyřazuje i „výhodou“; nově nepřetržitý a třísměnný provoz; „řidič“ vyřadí vždy (WHITELIST „skladn“ zrušen); whitelist se neuplatní u ručně zadaných slov. Nehledají se už finance/pojištění ani státní zaměstnavatelé (dřív `TARGET`/`STATE_RE`), jen obory z `profese.json`.
- Opravená chyba: regex řidičského průkazu nechytal skloňování („řidičského průkazu“).
- Starší řádky v Notionu zůstávají (např. s „řidičský průkaz výhodou“).

## Portály
- jobs.cz: parser přes CSS (ve výpisu není JSON-LD; parsery JSON-LD nečtou, jen CSS), stránkování `&page=N`. Občas nahodile vrátí prázdnou stránku (`layout_changed`); je jeden retry, případně běh zopakovat. Kostelec nad Orlicí portál nezná (404 = jen tato lokalita prázdná).
- jenprace.cz: filtr `?locations[0]=<ID>&subjects[0]=<slovo>`; ID měst v `JENPRACE_CZ["city_ids"]` (Rychnov nad Kněžnou 732882, Kostelec nad Orlicí 732850, Hradec Králové 732549, Pardubice 733145; z `data-locations.json`). Za nadpisem „Podívejte se i na jiné nabídky“ jsou jen doporučené nabídky (parser je vynechává); „(0 inzerátů)“ = prázdný výsledek. Filtr podle slova propouští i nesouvisející obory.
- Portály vracejí k lokalitám i široké okolí (Kolín, Havlíčkův Brod…), proto se filtruje místo výkonu práce (`PLACE_WORDS`, `place_ok()`: čtyři města + Solnice, Vamberk, Častolovice, Doudleby n. Orl., Albrechtice n. Orl., Týniště n. Orl., Borohrádek, Opočno, Dobruška). Chrudim, Lázně Bohdaneč, kraje a „Celá ČR“ neprojdou; prázdné místo projde.
- `fetch_detail_text` odstraní `header`, `nav`, `footer`: patička jenprace.cz má „Top profese“ s „Obsluha vysokozdvižných vozíků“ a „Řidič / Řidička“, jinak by se vyřadila každá nabídka (ověřeno živě 2026-11-11).
- Stránkování nekončí na „stránka bez nových nabídek“ (překryv měst), jen na prázdné stránce, starých nabídkách nebo `MAX_PAGES`.

## Notion, Telegram
- Archivace duplicit jde přes `core._send(..., "PATCH", ...)` (jen `api.notion.com`), výběr duplicit je `split_duplicates()`.
- Telegram: `--notify` a bot ověřeny; bot musí od uživatele dostat `/start`. Zprávy jdou jako HTML, první řádek tučně, texty se escapují. Shrnutí skládá jen `core.summary()` (CLI, MCP, bot), do Telegramu jde přes `core.notify_done()`; seznam nabídek se neposílá. Selhání Telegramu nesmí shodit CLI.
- Rate limit: HTTP 429 z Notionu/Telegramu se opakuje (`_send`, `Retry-After`, max 5 pokusů).
- Zámek `.run.lock` starší než hodina se ignoruje (po spadlém běhu).

## Evidence změn slovníku (nejnovější nahoře)
- 2026-11-24 (na zadání, podle dry-runu 4): `PLACE_WORDS` +Holice, Býšť; home office v názvu pustí místo na úrovni kraje/ČR; kat. 1 title +`customer care/service`, `klientský servis/podpora`, `infocentr` (search +3 dotazy); kat. 2 +`sekretář`, `metodická podpora … fakturace` (search +`sekretářka`); kat. 4 +`prodejce mobilních telefonů / ICT` (search +1). `NONSTOP_RE` zúžen: holé „Hotline 24/7“ a „nonstop poradna“ už nevyřazují (Kaufland, motopůjčovna).\n- 2026-11-24 (na zadání): kat. 1 +IT podpora jen L1/L2 (title `IT podpor|support|helpdesk`, nebo `L1/L2` + podpora; vyloučeno L3/senior/expert/architekt/vývojář/vedoucí/manažer), search +`IT podpora`; kat. 2 +asistent nákupčího, project administrator / projektový administrátor (hraniční, CV schvaluje a odesílá uživatel), search +2 dotazy. Řidičák: vyřazuje jen požadavek, „výhodou“ projde (`LICENCE_OK_RE`, `_licence_hit` v core.py). Důvod: filtr bral zajímavé nabídky, kde ŘP není podmínka (v dry-runu 3 bylo 56 vyřazení kvůli ŘP).\n- 2026-11-24: nálezy z detailu VYPNUTY (`signaly` → `signaly_vypnuto` v kat. 1–4). Důvod: dry-run 2 a 3 (po zúžení) znovu 4 z 4 šum (SERVISBAL, Foxconn ×3), 0 užitečných. Bez diagnostiky (která signály zasáhly) se ladí naslepo. Zapnutí: přejmenovat klíč zpět; testy mají pomocníka `_profiles_with_detail_discovery`.
- 2026-11-24: `profese.json` po prvním plném dry-runu (šum: Foxconn, SERVISBAL, Manuvia, 5× prodavač, revizní technik): kat. 4 −`prodavač` (není v zadání), signály kat. 4 −`elektronik`, `tarif` jen jako slovo; kat. 1 title „technická podpora“ ne před „projekt/realizac“. Detail zůstává zapnutý (MIN_SIGNALS=2), ověřit dalším během.
- 2026-11-24: `blacklist.txt` = sloučené `BLACKLIST` + `BLACKLIST_HARD` beze změny obsahu (+ viz výše).
- 2026-11-20: `BLACKLIST` +zámečník, svářeč, autoklempíř, instalatér, topenář, montér, elektromontáž, plynovod, realit, makléř, investiční specialist, systémový inženýr, informačních systémů (šum z ostrého běhu; na pokyn uživatele). Už zapsané řádky v Notionu zůstávají.
- 2026-11-20: `prodavač` odebráno z `BLACKLIST_HARD` (přání uživatele).
- 2026-11-19: přejmenování seznamů na `BLACKLIST` / `BLACKLIST_HARD` / `WHITELIST`, obsah beze změny.

## Běhy
- 2026-11-24 (dry-run 4, po db21c28): 758 vyřazeno (lokalita 584, bez shody 66, řidičák 53, blacklist 27, VZV 12, noc 10, nepřetržitý 5, pravidlo 1). Zdroje chyb viz Známé věci a evidence výše.
- 2026-11-24 (dry-run 3, po zúžení šumu, commit d3f2c85): 899 kandidátů, 14 nových, 152 existovalo, 733 vyřazeno (lokalita 566, bez shody 57, řidičák 56, blacklist 27, VZV 11, noc 10, nepřetržitý 5, pravidlo 1). Z detailu 4 (všechny šum). Pozn.: dry-run 2 běžel na staré verzi (spuštěn před git pull).
- 2026-11-24 (první plný dry-run podle profesí, před úpravou signálů): 898 kandidátů (jobs.cz 550, jenprace.cz 348), 21 nových, 154 už existovalo, 723 vyřazeno (lokalita 565, řidičák 54, bez shody 50, blacklist 27, VZV 11, noc 10, nepřetržitý 5, pravidlo 1). Nálezy z detailu: 5, všech 5 šum.
- 2026-11-06 (první): 289 nově zapsaných, 1195 už existovalo.
- 2026-11-06 (s archivací duplicit): 5 nově zapsaných, 1448 už existovalo, 35 vyřazeno filtrem oborů, 17 duplicit archivováno.

## Známé věci
- Falešné VZV z bloků „podobné nabídky“/příbuzné profese na jenprace.cz (dry-run 4: 4 řádky). Čeká na HTML detailu k úpravě `fetch_detail_text`.
- Název „bez nočních směn“ nepřebíjí tabulku příplatků v detailu agentur (dry-run 4: Technik výstupního měření). Neřešeno, čeká na rozhodnutí.
- Detail celé stránky může obsahovat bloky „podobné nabídky“; zmínka VZV/noční tam může vyřadit nabídku zbytečně. Ověřit v `logs/vyrazene_dryrun.csv`.
- Dřívější zkušenost: relevance „jen podle detailu“ (krátký/chybějící detail nebo obecné zásahy) pouštěla šum (montér plynovodů, autoklempíř). Nálezy z detailu proto vyžadují `MIN_SIGNALS` různých signálů, kategorie 5 se z detailu nehledá vůbec a blacklist platí vždy. Signály ladit podle `logs/vyrazene_dryrun.csv`.
