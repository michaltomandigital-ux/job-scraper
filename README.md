# job-scraper

Osobní nástroj, který automaticky hledá pracovní nabídky na **jobs.cz** a **jenprace.cz**, vyhodnotí je podle mých pravidel a vhodné uloží do databáze v **Notionu**. Shrnutí každého běhu pošle na **Telegram**.

Projekt jsem postavil s pomocí AI (**Claude Code**): zadával jsem požadavky a pravidla, kontroloval výstupy, testoval na skutečných datech a postupně ladil logiku rozhodování.

## Co skript dělá

1. **Hledá** na portálech podle seznamu oborů a dotazů v `profese.json` (nebo podle zadaných slov) ve vybraných lokalitách, nanejvýš 14 dní staré nabídky.
2. **Rozhoduje** o každé nové nabídce v jediné funkci `core.decide()`:
   - lokalita (výjimka: home office na úrovni kraje / ČR),
   - blacklist výrazů v názvu (`blacklist.txt`),
   - tvrdá vyřazení: řidičák jako požadavek, VZV, noční směny, nepřetržitý a třísměnný provoz,
   - shoda s obory v názvu, případně pravidla podle textu detailu (např. provize, práce na IČO),
   - každé vyřazení má důvod a zapíše se do auditního logu.
3. **Ukládá** vhodné nabídky do Notionu (kategorie, skóre, stav „Nový“) a hlídá duplicity – stejná pozice na dvou portálech je jeden řádek, novější inzerát nahradí starší.
4. **Oznamuje** výsledek běhu (počty nových, vyřazených a podle důvodu) na Telegram.

## Etika a bezpečnost

- Skript jen čte: **nikdy neodpovídá na inzeráty** ani nekontaktuje zaměstnavatele.
- Respektuje `robots.txt` portálů, mezi požadavky dělá pauzu 2 s a posílá poctivý User-Agent.
- Odesílat data smí jen na API Notionu a Telegramu (hlídáno v kódu i testem).
- Tokeny jsou jen v `.env` / GitHub Secrets, nikdy v repozitáři.

## Spouštění

```bash
pip install -r requirements.txt
cp .env.example .env            # doplnit NOTION_TOKEN, NOTION_DATABASE_ID, TELEGRAM_*
python scrape.py --dry-run      # zkušební běh, nic nezapisuje
python scrape.py --notify       # ostrý běh: zápis do Notionu + shrnutí na Telegram
python scrape.py "zákaznická podpora" --dry-run   # hledání jen zadaných slov
```

Další způsoby spuštění:
- **GitHub Actions** (`.github/workflows/scrape.yml`) – automaticky každý druhý den, ručně přes „Run workflow“.
- **Telegram bot** (`telegram_bot.py`) – příkaz `/hledej <slova>`.
- **MCP server** (`mcp_server.py`) – nástroj `run_scrape` pro AI asistenty (např. Claude).

## Struktura

| Soubor | Účel |
|---|---|
| `core.py` | veškerá logika: parsery portálů, robots.txt, `decide()`, Notion, Telegram |
| `profese.json` | obory: dotazy, shoda v názvu, pravidla podle detailu |
| `blacklist.txt` | výrazy v názvu pozice, které se vždy vyřadí |
| `scrape.py` | příkazová řádka |
| `test_core.py` | offline testy (`pytest`) nad uloženými stránkami portálů |
