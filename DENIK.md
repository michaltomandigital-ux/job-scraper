# Deník a technické poznámky (mimo CLAUDE.md)

Zadání a pravidla jsou v `CLAUDE.md`. Tady je historie a to, co se ověřilo na živých portálech.

## 2026-11-24: profese, decide(), vyřazení podle zadání
- Whitelist oborů a signály z detailu v `profese.json`; blacklist v `blacklist.txt` (dřív `EXCLUDE_TITLE_WORDS`).
- Jedna rozhodovací funkce `decide()` místo rozházených filtrů; důvod vyřazení v `logs/vyrazene*.csv`.
- Změny chování proti dřívějšku: řidičské oprávnění vyřazuje i „výhodou“ (dřív jen jako podmínka); nově nepřetržitý a třísměnný provoz; „řidič“ v názvu vyřadí vždy (dřív měl přednost „skladník“/„prodavač“); „pokladní“ už není v blacklistu; whitelist neuplatněn u ručně zadaných slov.
- Opravená chyba: dřívější regex řidičského průkazu nechytal skloňování („řidičského průkazu“).
- Starší řádky v Notionu zůstávají (např. s „řidičský průkaz výhodou“).

## Portály
- jobs.cz: parser přes CSS (ve výpisu není JSON-LD), stránkování `&page=N`. Občas nahodile vrátí prázdnou stránku (`layout_changed`); je jeden retry, případně běh zopakovat. Kostelec nad Orlicí portál nezná (404 = jen tato lokalita prázdná).
- jenprace.cz: filtr `?locations[0]=<ID>&subjects[0]=<slovo>`; ID měst v `JENPRACE_CZ["city_ids"]` (Rychnov nad Kněžnou 732882, Kostelec nad Orlicí 732850, Hradec Králové 732549, Pardubice 733145; z `data-locations.json`). Za nadpisem „Podívejte se i na jiné nabídky“ jsou jen doporučené nabídky (parser je vynechává); „(0 inzerátů)“ = prázdný výsledek. Filtr podle slova propouští i nesouvisející obory.
- Portály vracejí k lokalitám i široké okolí (Kolín, Havlíčkův Brod…), proto se filtruje místo výkonu práce (`PLACE_WORDS`, `place_ok()`); prázdné místo projde.
- `fetch_detail_text` odstraní `header`, `nav`, `footer`: patička jenprace.cz má „Top profese“ s „Obsluha vysokozdvižných vozíků“ a „Řidič / Řidička“, jinak by se vyřadila každá nabídka (ověřeno živě 2026-11-11).

## Notion, Telegram
- Notion: dedup podle URL; při každém běhu se archivují duplicity (stejný název+firma+místo; zůstává nejstarší, archivace = koš Notionu, obnovitelné; řádky s jiným stavem než „Nový“ se nearchivují, `split_duplicates()`). Nová nabídka, jejíž pozice už v Notionu je, se nezapíše.
- Telegram: `--notify` a bot ověřeny; bot musí od uživatele dostat `/start`. Zprávy jdou jako HTML, první řádek s počtem tučně, názvy a URL se escapují. Shrnutí skládá jen `core.summary()` (CLI, MCP, bot); do Telegramu jde přes `core.notify_done()`. Bot `/hledej` posílá navíc seznam nových nabídek. Selhání Telegramu nesmí shodit CLI.
- Rate limit: HTTP 429 z Notionu/Telegramu se opakuje (`_send`, `Retry-After`, max 5 pokusů).

## Běhy
- 2026-11-06 (první): 289 nově zapsaných, 1195 už existovalo.
- 2026-11-06 (s archivací duplicit): 5 nově zapsaných, 1448 už existovalo, 35 vyřazeno filtrem oborů, 17 duplicit archivováno.

## Známé věci
- Cross-portálové duplicity (stejná pozice na více portálech) se nededuplikují, pokud se liší název/firma/místo ve formátu.
- Detail celé stránky může obsahovat bloky „podobné nabídky“; zmínka VZV/noční tam může vyřadit nabídku zbytečně. Ověřit v `logs/vyrazene_dryrun.csv`.
