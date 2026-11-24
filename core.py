"""Scraper pracovních nabídek: hledá, parsuje, ukládá do Notionu.

NIKDY neodesílá reakce na inzeráty. POST jde jen na api.notion.com a api.telegram.org.
"""
import csv
import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ---------------------------------------------------------------- konstanty
CITIES = ["Hradec Králové", "Pardubice", "Rychnov nad Kněžnou", "Kostelec nad Orlicí"]
MAX_POSTED_DAYS = 14
MAX_PAGES = 5
REQUEST_PAUSE = 2.0  # min. pauza mezi požadavky na jeden portál (s)
USER_AGENT = "JobScraperPersonal/1.0 (personal non-commercial job search; GET only)"

NOTION_VERSION = "2025-09-03"
COL_TITLE = "Název pozice"
COL_COMPANY = "Firma"
COL_PLACE = "Místo výkonu práce"
COL_PORTAL = "Pracovní portál"
COL_URL = "URL pozice"
COL_STATUS = "Stav"
STATUS_NEW = "Nový"
# Volitelné sloupce (API je nevytvoří, přidej ručně): bez nich se příslušná hodnota prostě nezapíše.
COL_CATEGORY = "Kategorie"  # select
COL_SCORE = "Skóre"  # number
COL_SOURCE = "Nalezeno z"  # select: "název" / "detail"
OPTIONAL_COLS = {COL_CATEGORY: "select", COL_SCORE: "number", COL_SOURCE: "select"}

ALLOWED_POST_HOSTS = {"api.notion.com", "api.telegram.org"}

# --- selektory portálů (při změně layoutu upravit jen zde)
JOBS_CZ = {
    "name": "jobs.cz",
    "base": "https://www.jobs.cz",
    "search": "https://www.jobs.cz/prace/{city_slug}/?q%5B%5D={kw}&page={page}",
    "card": "article.SearchResultCard",
    "link": "a.SearchResultCard__titleLink",
    "status": ".SearchResultCard__status",
    "footer_items": "li.SearchResultCard__footerItem",
}

JENPRACE_CZ = {
    "name": "jenprace.cz",
    "base": "https://www.jenprace.cz",
    # ID lokalit jsou z kanonických URL portálu (filtr města)
    "city_ids": {"Hradec Králové": "732549", "Pardubice": "733145",
                 "Rychnov nad Kněžnou": "732882", "Kostelec nad Orlicí": "732850"},
    "search": "https://www.jenprace.cz/nabidky?locations%5B0%5D={city_id}&subjects%5B0%5D={kw}&page={page}",
    "card": "article.item",
    "link": "a[data-cy=offer-link-label]",
    "title": ".offer-link",
    "company": "[data-cy=offer-ownership-company]",
    "locality": "[data-cy=offer-locality]",
    "date": "[data-cy=offer-date-created]",
    "stop_heading": "Podívejte se i na jiné nabídky",  # za tímto nadpisem jsou jen doporučené nabídky
}

# Pravidla rozhodování (viz decide()):
# - blacklist.txt: výrazy v NÁZVU, které nabídku vyřadí vždy; profese.json: whitelist oborů, synonyma, signály z detailu.
# - Tvrdá pravidla (název i detail): jakákoli zmínka o řidičském oprávnění, VZV, noční směny, nepřetržitý a třísměnný provoz.
#   Dvousměnný provoz je v pořádku.
# VZV (vysokozdvižný vozík): jakákoli zmínka vyřadí, i "VZV výhodou" a "bez nutnosti VZV".
VZV_RE = re.compile(r"\bVZV\b|vysokozdvižn", re.I)
# Noční směny: "nočn" jen na začátku slova, ať "vánoční" neprojde jako noční.
NIGHT_RE = re.compile(r"\bnočn", re.I)
# Zápor na STEJNÉM řádku ruší noční směnu / nepřetržitý / třísměnný provoz ("bez nočních", "není nepřetržitý provoz").
NIGHT_NEG_RE = re.compile(r"\bbez\b|\bnení\b|\bnejsou\b|\bnebud|\bžádn|\bnemus|vyhn", re.I)
NONSTOP_RE = re.compile(r"nepřetržit\w*\s+(provoz|směn)|\b24\s*/\s*7\b|non-?stop|provoz\w*\s+24\s*hod|24\s*hodin\w*\s+provoz", re.I)
THREE_SHIFT_RE = re.compile(r"třísměnn|tří\s*směnn|\b3\s*směn|tři\s+směn|ve\s+třech\s+směnách", re.I)

# Místo výkonu práce: nabídka se uloží, jen když místo obsahuje některý z těchto výrazů
# (bez ohledu na velikost písmen; část města např. "Pardubice – Polabiny" projde). Prázdné místo projde.
# Kraje a "Celá ČR" neprojdou. Okolí Rychnova a Kostelce nad Orlicí: obce kolem nich.
PLACE_WORDS = [
    "hradec králové", "pardubice", "rychnov nad kněžnou", "kostelec nad orlicí",
    "solnice", "vamberk", "častolovice", "doudleby nad orlicí", "albrechtice nad orlicí",
    "týniště nad orlicí", "borohrádek", "opočno", "dobruška",
]

# Řidičské oprávnění: JAKÁKOLIV zmínka vyřadí nabídku (i "výhodou", "vítán", "není podmínkou").
LICENCE_RE = re.compile(r"řidičsk\w*\s+(průkaz\w*|oprávnění)|řidičák|\bŘP\b|oprávnění\s+k\s+řízení", re.I)

# Datové soubory a rozhodování
ROOT = Path(__file__).parent
PROFILES_FILE = ROOT / "profese.json"
BLACKLIST_FILE = ROOT / "blacklist.txt"
LOG_DIR = ROOT / "logs"  # v .gitignore
MIN_SIGNALS = 2  # kolik RŮZNÝCH signálů v detailu stačí k nalezení pozice bez shody v názvu
TITLE_SCORE = 10  # základ skóre při shodě v názvu
REASON_LABELS = {
    "lokalita": "lokalita", "blacklist": "blacklist", "ridicak": "řidičské oprávnění", "vzv": "VZV",
    "noc": "noční směny", "nepretrzity": "nepřetržitý provoz", "trismenny": "třísměnný provoz",
    "pravidlo": "pravidlo kategorie", "bez_shody": "bez shody s obory", "detail_nedostupny": "detail nedostupný",
}
TRANSIENT_REASONS = {"detail_nedostupny"}  # dočasné: nelogovat do skip-listu, příště se vyhodnotí znovu

CZ_MONTHS = {
    "ledna": 1, "února": 2, "března": 3, "dubna": 4, "května": 5, "června": 6,
    "července": 7, "srpna": 8, "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
}


@dataclass
class Job:
    title: str
    company: str
    place: str
    portal: str
    url: str
    posted: date | None = None
    category: str = ""  # vyplní decide() přes run()
    score: int = 0
    source: str = ""  # "název" / "detail"


class LayoutChanged(Exception):
    pass


class RobotsDisallowed(Exception):
    pass


# ---------------------------------------------------------------- pomocné
def slugify(city: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", city).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", "-", s.strip().lower())


def parse_cz_date(text: str, today: date | None = None) -> date | None:
    """'Přidáno dnes' / 'Přidáno včera' / '22. října' -> date, jinak None."""
    today = today or date.today()
    t = text.lower()
    if re.search(r"dne[sš]", t):
        return today
    if re.search(r"včera|včerejš", t):
        return today - timedelta(days=1)
    m = re.search(r"(\d{1,2})\.\s*([a-zěščřžýáíéúůťďňó]+)", t)
    if m and m.group(2) in CZ_MONTHS:
        try:
            d = date(today.year, CZ_MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:  # 29. února v nepřestupném roce: jinak by spadl celý portál
            return None
        return d if d <= today else d.replace(year=today.year - 1)
    return None


def place_ok(place: str) -> bool:
    """True, když je místo výkonu práce v povolené lokalitě (PLACE_WORDS); prázdné místo projde."""
    p = place.lower().strip()
    return not p or any(w in p for w in PLACE_WORDS)


def _line_hit(text: str, pos: re.Pattern, neg: re.Pattern, look_next: bool = False) -> bool:
    """True, když některý řádek odpovídá `pos` a zápor `neg` na něm není (s look_next ani na dalším řádku)."""
    lines = text.split("\n")
    return any(pos.search(line) and not neg.search(line + " " + nxt if look_next else line)
               for line, nxt in zip(lines, lines[1:] + [""]))


@dataclass
class Decision:
    keep: bool
    reason: str  # kód z REASON_LABELS, "ok" nebo "detail_neovereno"
    category: str = ""
    score: int = 0
    source: str = ""  # "název" / "detail"
    note: str = ""  # upřesnění důvodu (zasažený výraz blacklistu apod.)


def hard_reason(text: str) -> str | None:
    """Kód důvodu vyřazení podle tvrdých pravidel (název i detail), jinak None. Dvousměnný provoz projde."""
    if LICENCE_RE.search(text):
        return "ridicak"
    if VZV_RE.search(text):
        return "vzv"
    if _line_hit(text, NIGHT_RE, NIGHT_NEG_RE):
        return "noc"
    if _line_hit(text, NONSTOP_RE, NIGHT_NEG_RE):
        return "nepretrzity"
    if _line_hit(text, THREE_SHIFT_RE, NIGHT_NEG_RE):
        return "trismenny"
    return None


def load_profiles(path: Path = PROFILES_FILE) -> list[dict]:
    """Kategorie z profese.json (pořadí = priorita) s předkompilovanými výrazy."""
    comp = lambda items: [re.compile(p, re.I) for p in items]
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [{"nazev": c["nazev"], "search": c["search"], "title": comp(c["title"]),
             "signaly": comp(c.get("signaly", [])),
             "pravidla": [{"title": re.compile(r["title"], re.I), "vyradit": comp(r["vyradit_pokud_detail"])}
                          for r in c.get("pravidla", [])]}
            for c in data["kategorie"]]


def load_blacklist(path: Path = BLACKLIST_FILE) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [w.strip().lower() for w in lines if w.strip() and not w.lstrip().startswith("#")]


def search_keywords(profiles: list[dict]) -> list[str]:
    """Dotazy na portály ze všech kategorií (bez duplicit, v pořadí priority)."""
    return list(dict.fromkeys(k for c in profiles for k in c["search"]))


def _signal_hits(text: str, cat: dict) -> int:
    return sum(1 for p in cat["signaly"] if p.search(text))


def decide(job: "Job", fetch, profiles: list[dict] | None = None, blacklist=()) -> Decision:
    """JEDINÉ místo, kde se rozhoduje o nabídce: zařadit/vyřadit + důvod.

    `fetch()` vrací text detailu (nebo None = nelze ověřit); volá se nejvýš jednou a jen když je detail potřeba.
    `profiles=None` = ruční hledání zadaných slov: bez whitelistu oborů, platí blacklist, lokalita a tvrdá pravidla.
    """
    title = job.title.lower()
    if not place_ok(job.place):
        return Decision(False, "lokalita")
    hit = next((w for w in blacklist if w in title), None)
    if hit:
        return Decision(False, "blacklist", note=hit)
    reason = hard_reason(job.title)
    if reason:
        return Decision(False, reason)
    cat = next((c for c in profiles or [] if any(p.search(title) for p in c["title"])), None)
    text = fetch()
    if text is None:  # detail nešlo ověřit: shoda v názvu / ruční hledání se uloží (a počítá se), jinak nelze rozhodnout
        if cat or not profiles:
            return Decision(True, "detail_neovereno", cat["nazev"] if cat else "", TITLE_SCORE if cat else 0,
                            "název" if cat else "")
        return Decision(False, "detail_nedostupny")
    reason = hard_reason(text)
    if reason:
        return Decision(False, reason)
    if not profiles:
        return Decision(True, "ok")
    if cat:
        for rule in cat["pravidla"]:
            if rule["title"].search(title) and any(p.search(text) for p in rule["vyradit"]):
                return Decision(False, "pravidlo", note=rule["title"].pattern)
        return Decision(True, "ok", cat["nazev"], TITLE_SCORE + min(_signal_hits(text, cat), 5), "název")
    hits, best = max(((_signal_hits(text, c), c) for c in profiles), key=lambda x: x[0])  # shoda -> vyšší priorita
    if hits >= MIN_SIGNALS:
        return Decision(True, "ok", best["nazev"], hits, "detail")
    return Decision(False, "bez_shody")


def load_recent_rejects(today: date | None = None) -> set[str]:
    """URL nabídek vyřazených v posledních MAX_POSTED_DAYS dnech (logs/vyrazene.csv): nestahují se znovu."""
    cutoff = ((today or date.today()) - timedelta(days=MAX_POSTED_DAYS)).isoformat()
    try:
        with open(LOG_DIR / "vyrazene.csv", encoding="utf-8-sig", newline="") as f:
            return {row[2] for row in csv.reader(f, delimiter=";") if len(row) > 2 and row[0] >= cutoff}
    except OSError:
        return set()


def write_reject_log(rows: list[list[str]], dry_run: bool) -> None:
    """Ostrý běh přidává do logs/vyrazene.csv (skip-list + audit), --dry-run přepíše logs/vyrazene_dryrun.csv."""
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / ("vyrazene_dryrun.csv" if dry_run else "vyrazene.csv")
    new_file = dry_run or not path.exists()
    with open(path, "w" if dry_run else "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        if new_file:
            w.writerow(["datum", "portál", "url", "název", "firma", "místo", "důvod", "upřesnění"])
        w.writerows(rows)


def fetch_detail_text(client: httpx.Client, job: "Job", robots_cache: dict) -> str | None:
    """Stáhne detail nabídky (GET, s pauzou, po kontrole robots.txt) jako text. None = nelze ověřit."""
    u = urlparse(job.url)
    try:
        if u.netloc not in robots_cache:  # detail může být na jiném hostiteli než výpis
            robots_cache[u.netloc] = robots_parser(client, f"{u.scheme}://{u.netloc}")
        if not robots_cache[u.netloc].can_fetch(USER_AGENT, job.url):
            return None
        time.sleep(REQUEST_PAUSE)
        r = client.get(job.url)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for el in soup.select("header, nav, footer"):  # menu/patička portálu (jenprace.cz: "Top profese" s VZV, řidičem)
            el.decompose()
        return soup.get_text("\n", strip=True)
    except httpx.HTTPError:
        return None


# ---------------------------------------------------------------- portály
def parse_jobs_cz(html: str, today: date | None = None) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []
    for card in soup.select(JOBS_CZ["card"]):
        a = card.select_one(JOBS_CZ["link"])
        if not a or not a.get("href"):
            continue
        href = urljoin(JOBS_CZ["base"], a["href"])
        m = re.search(r"/rpd/(\d+)", href)
        url = f"{JOBS_CZ['base']}/rpd/{m.group(1)}/" if m else href  # bez /rpd/ zůstává query (nese ID)
        items = [li.get_text(" ", strip=True) for li in card.select(JOBS_CZ["footer_items"])]
        st = card.select_one(JOBS_CZ["status"])
        jobs.append(Job(
            title=a.get_text(strip=True),
            company=items[0] if items else "",
            place=items[1] if len(items) > 1 else "",
            portal=JOBS_CZ["name"],
            url=url,
            posted=parse_cz_date(st.get_text(" ", strip=True), today) if st else None,
        ))
    return jobs


def parse_jenprace_cz(html: str, today: date | None = None) -> list[Job]:
    cfg = JENPRACE_CZ
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []
    first = soup.select_one(cfg["card"])
    container = first.parent if first else None
    if container is None:
        return []
    h1 = soup.select_one("h1")
    if h1 and re.search(r"\(0 inzerát", h1.get_text()):
        return []  # portál přesto ukazuje doporučené nabídky, ty nechceme
    clean = lambda s: re.sub(r"\s+", " ", s).strip()
    for el in container.find_all(recursive=False):
        if el.name in ("h2", "h3") and cfg["stop_heading"] in el.get_text():
            break
        if el.name != "article":
            continue
        a = el.select_one(cfg["link"])
        if not a or not a.get("href"):
            continue
        title = el.select_one(cfg["title"])
        comp = el.select_one(cfg["company"])
        loc = el.select_one(cfg["locality"])
        dt = el.select_one(cfg["date"])
        jobs.append(Job(
            title=clean(title.get_text()) if title else clean(a.get_text()),
            company=clean(comp.get_text()).rstrip("| ").strip() if comp else "",
            place=re.sub(r"\s*\(\d+\s*km\)", "", clean(loc.get_text())) if loc else "",
            portal=cfg["name"],
            url=urljoin(cfg["base"], a["href"]).split("?")[0],
            posted=parse_cz_date(dt.get_text(" ", strip=True), today) if dt else None,
        ))
    return jobs


# portál -> (konfigurace, parser). Další portály se přidají sem.
PORTALS = {
    "jobs.cz": (JOBS_CZ, parse_jobs_cz),
    "jenprace.cz": (JENPRACE_CZ, parse_jenprace_cz),
}


def robots_parser(client: httpx.Client, base: str) -> RobotFileParser:
    rp = RobotFileParser()
    r = client.get(urljoin(base, "/robots.txt"))
    if r.status_code in (401, 403) or r.status_code >= 500:  # nelze ověřit = zakázáno
        rp.disallow_all = True
    else:
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    return rp


def search_portal(client: httpx.Client, name: str, keywords: list[str]) -> tuple[list[Job], int]:
    """Vrací (nabídky, počet selhaných dotazů)."""
    cfg, parser = PORTALS[name]
    robots = robots_parser(client, cfg["base"])  # robots.txt jednou na portál, před hledáním; platí pro každé URL
    found: dict[str, Job] = {}
    any_allowed = False
    saw_cards = empty_200 = False
    failed, last_error = 0, None
    cutoff = date.today() - timedelta(days=MAX_POSTED_DAYS)
    for kw in keywords:
        for city in CITIES:
            for page in range(1, MAX_PAGES + 1):
                url = cfg["search"].format(city_slug=slugify(city), kw=quote_plus(kw), page=page,
                                           city_id=cfg.get("city_ids", {}).get(city, ""))
                if not robots.can_fetch(USER_AGENT, url):  # zakázané URL se přeskočí, nikdy neobchází
                    break
                any_allowed = True
                time.sleep(REQUEST_PAUSE)
                try:
                    r = client.get(url)
                    if r.status_code == 404:  # portál lokalitu nezná (např. jobs.cz Kostelec nad Orlicí) -> jen tato lokalita je prázdná
                        break
                    r.raise_for_status()
                    jobs = parser(r.text)
                    if not jobs and page == 1 and not found:
                        time.sleep(REQUEST_PAUSE)  # jeden opakovaný pokus (občasná prázdná stránka)
                        r = client.get(url)
                        r.raise_for_status()
                        jobs = parser(r.text)
                except httpx.HTTPError as e:  # chyba jednoho dotazu nezahodí ostatní výsledky portálu
                    failed, last_error = failed + 1, e
                    break
                if not jobs:
                    empty_200 = empty_200 or page == 1
                    break
                saw_cards = True
                new = [j for j in jobs if j.url not in found]
                all_dated_old = all(j.posted and j.posted < cutoff for j in jobs)
                for j in new:
                    if j.posted is None or j.posted >= cutoff:
                        found[j.url] = j
                if not new or all_dated_old:  # nové stránky už nic nepřinesou
                    break
    if not any_allowed:
        raise RobotsDisallowed(name)
    if not found and last_error:
        raise last_error
    if not found and not saw_cards and empty_200:  # HTTP 200, ale žádné karty nikde = podezření na změnu layoutu
        raise LayoutChanged(name)
    return list(found.values()), failed


# ---------------------------------------------------------------- Notion
def _send(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    """Jediné místo, odkud jde POST/PATCH. POST jen na api.notion.com a api.telegram.org, PATCH jen na Notion."""
    allowed = ALLOWED_POST_HOSTS if method == "POST" else {"api.notion.com"}
    if method not in ("POST", "PATCH") or urlparse(url).hostname not in allowed:
        raise RuntimeError(f"{method} na {urlparse(url).hostname} je zakázán")  # bez celé URL: Telegram má token v cestě
    for attempt in range(5):  # rate limit (429): počkat podle Retry-After a zkusit znovu
        r = client.request(method, url, **kw)
        if r.status_code != 429:
            break
        time.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
    return r


class Notion:
    optional: frozenset = frozenset()  # volitelné sloupce, které v databázi opravdu jsou (OPTIONAL_COLS)

    def __init__(self, token: str, database_id: str):
        self.c = httpx.Client(
            base_url="https://api.notion.com/v1",
            headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION},
            timeout=30,
        )
        db = self.c.get(f"/databases/{database_id}")
        if db.status_code != 200:
            raise SystemExit(f"Notion: databázi {database_id} nelze načíst ({db.status_code}): "
                             f"{db.text[:200]} (ověř ID databáze, ne pohledu, a sdílení integrace)")
        sources = db.json().get("data_sources") or []
        if not sources:
            raise SystemExit("Notion: databáze nemá žádný data source")
        self.ds_id = sources[0]["id"]
        ds = self.c.get(f"/data_sources/{self.ds_id}")
        if ds.status_code != 200:
            raise SystemExit(f"Notion: data source nelze načíst ({ds.status_code}), ověř sdílení integrace")
        props = ds.json().get("properties", {})
        self._check_schema(props)
        self.optional = frozenset(c for c, t in OPTIONAL_COLS.items() if props.get(c, {}).get("type") == t)

    @staticmethod
    def _check_schema(props: dict):
        for col in (COL_TITLE, COL_COMPANY, COL_PLACE, COL_PORTAL, COL_URL, COL_STATUS):
            if col not in props:
                raise SystemExit(f"Notion: chybí sloupec '{col}'")
        opts = [o["name"] for o in props[COL_STATUS].get("status", {}).get("options", [])]
        if STATUS_NEW not in opts:
            raise SystemExit(f"Notion: ve sloupci '{COL_STATUS}' neexistuje hodnota '{STATUS_NEW}' "
                             f"(API ji nevytvoří, přidej ji ručně). Existující: {opts}")

    def load_rows(self) -> list[dict]:
        """Všechny řádky databáze: id, url, klíč (název, firma, místo), čas vytvoření."""
        rows, cursor = [], None
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            r = _send(self.c, "POST", f"https://api.notion.com/v1/data_sources/{self.ds_id}/query", json=body)
            r.raise_for_status()
            data = r.json()
            for p in data["results"]:
                props = p["properties"]
                text = lambda col: "".join(x.get("plain_text", "") for x in props.get(col, {}).get("title")
                                           or props.get(col, {}).get("rich_text") or [])
                key = tuple(s.strip().lower() for s in (text(COL_TITLE), text(COL_COMPANY), text(COL_PLACE)))
                status = (props.get(COL_STATUS, {}).get("status") or {}).get("name", "")
                rows.append({"id": p["id"], "url": props.get(COL_URL, {}).get("url") or "",
                             "key": key, "created": p.get("created_time", ""), "status": status})
            if not data.get("has_more"):
                return rows
            cursor = data["next_cursor"]

    def archive(self, page_id: str):
        """Přesune řádek do koše Notionu (lze obnovit)."""
        r = _send(self.c, "PATCH", f"https://api.notion.com/v1/pages/{page_id}", json={"archived": True})
        r.raise_for_status()

    def create(self, j: Job):
        txt = lambda s: [{"text": {"content": s[:2000]}}]
        body = {
            "parent": {"type": "data_source_id", "data_source_id": self.ds_id},
            "properties": {
                COL_TITLE: {"title": txt(j.title)},
                COL_COMPANY: {"rich_text": txt(j.company)},
                COL_PLACE: {"rich_text": txt(j.place)},
                COL_PORTAL: {"select": {"name": j.portal}},
                COL_URL: {"url": j.url},
                COL_STATUS: {"status": {"name": STATUS_NEW}},
            }}
        if j.category:
            extra = {COL_CATEGORY: {"select": {"name": j.category}}, COL_SCORE: {"number": j.score},
                     COL_SOURCE: {"select": {"name": j.source}}}
            body["properties"].update({k: v for k, v in extra.items() if k in self.optional})
        r = _send(self.c, "POST", "https://api.notion.com/v1/pages", json=body)
        r.raise_for_status()


# ---------------------------------------------------------------- Telegram
def telegram_configured() -> bool:
    load_dotenv()
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def telegram_send(text: str) -> None:
    """Pošle zprávu (dělí na kusy do 4000 znaků) do TELEGRAM_CHAT_ID."""
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 4000:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    with httpx.Client(timeout=30) as c:
        for ch in chunks:
            r = _send(c, "POST", f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": ch, "disable_web_page_preview": True,
                            "parse_mode": "HTML"})
            if r.status_code != 200:  # bez raise_for_status: jeho zpráva by obsahovala token v URL
                try:
                    why = r.json().get("description", "")
                except ValueError:
                    why = ""
                raise RuntimeError(f"Telegram sendMessage selhal ({r.status_code}): {why}")


def notify_telegram(jobs: list[Job]) -> bool:
    """Pošle nové pozice; nic neposílá, když žádné nejsou nebo chybí tokeny. Vrací, zda poslal."""
    if not jobs or not telegram_configured():
        return False
    esc = lambda s: html.escape(s, quote=False)  # Telegram parse_mode=HTML: jen &, <, > musí být escapované
    lines = [f"<b>Nové pracovní nabídky: {len(jobs)}</b>", ""]  # první zpráva s počtem tučně
    for j in jobs:
        lines += [esc(j.title), f"{esc(j.company)} | {esc(j.place)}", esc(j.url), ""]
    telegram_send("\n".join(lines))
    return True


def summary(res: dict, dry_run: bool = False) -> str:
    """Prostý text shrnutí běhu (CLI, MCP, Telegram). Pro Telegram escapovat (notify_done)."""
    g = lambda k: res.get(k, 0)
    verb = "by se uložilo" if dry_run else "uloženo"
    portals = "; ".join(f"{n}: {s}" for n, s in res["portals"].items())
    exc = res.get("excluded") or {}
    why = ", ".join(f"{REASON_LABELS.get(k, k)} {v}" for k, v in sorted(exc.items(), key=lambda kv: -kv[1]))
    out = (f"Nových nabídek ({verb}): {len(res['new'])}, už existuje: {res['skipped_existing']}, "
           f"vyřazeno: {sum(exc.values())}" + (f" ({why})" if why else "") + f", chyby zápisu: {g('write_errors')}")
    if g("from_detail"):
        out += f"\nZ nových nalezeno z detailu (bez shody v názvu): {g('from_detail')}"
    if g("skipped_rejected"):
        out += f"\nPřeskočeno (nedávno vyřazeno): {g('skipped_rejected')}"
    if res.get("missing_columns"):
        out += f"\nV Notionu chybí sloupce (nezapsáno): {', '.join(res['missing_columns'])}"
    return out + f"\nPortály: {portals}"


def notify_done(res: dict) -> None:
    """Oznámení o dokončení scrapingu do Telegramu (posílá se i při 0 nových)."""
    telegram_send("<b>Scraping dokončen</b>\n" + html.escape(summary(res), quote=False))


# ---------------------------------------------------------------- hlavní běh
def split_duplicates(rows: list[dict]) -> tuple[dict, list[str]]:
    """Řádky -> ({klíč: ponechaný řádek}, [ID duplicit k archivaci]). Nejstarší zůstává."""
    keep, dups = {}, []
    # řádky s jiným stavem než "Nový" (uživatel s nimi pracoval) se nikdy nearchivují a mají přednost
    for row in sorted(rows, key=lambda r: (r["status"] == STATUS_NEW, r["created"])):
        if row["key"] in keep:
            if row["status"] == STATUS_NEW:
                dups.append(row["id"])
        else:
            keep[row["key"]] = row
    return keep, dups


def run(keywords: list[str] | None, dry_run: bool = False) -> dict:
    """Vrací {'new': [Job], 'skipped_existing': int, 'excluded': {důvod: počet}, 'portals': {name: stav}, ...}.

    Bez klíčových slov se hledá podle profese.json (whitelist oborů, signály z detailu); se zadanými slovy
    se hledá jen podle nich a whitelist se neuplatní (blacklist, lokalita a tvrdá pravidla platí vždy).
    """
    load_dotenv()
    token, db_id = os.getenv("NOTION_TOKEN"), os.getenv("NOTION_DATABASE_ID")
    if not token or not db_id:
        raise SystemExit("Chybí NOTION_TOKEN nebo NOTION_DATABASE_ID v .env")
    notion = Notion(token, db_id)  # kontrola schématu i ve --dry-run (jen čtení)
    profiles = None if keywords else load_profiles()
    keywords = keywords or search_keywords(profiles)
    blacklist = load_blacklist()

    result = {"new": [], "skipped_existing": 0, "skipped_rejected": 0, "excluded": {}, "from_detail": 0,
              "missing_columns": [c for c in OPTIONAL_COLS if c not in getattr(notion, "optional", OPTIONAL_COLS)],
              "portals": {}}
    candidates: dict[str, Job] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        for name in PORTALS:
            try:
                jobs, failed = search_portal(client, name, keywords)
                result["portals"][name] = f"ok ({len(jobs)})" + (f", {failed} dotazů selhalo" if failed else "")
                candidates.update({j.url: j for j in jobs})
            except RobotsDisallowed:
                result["portals"][name] = "robots_disallowed"
            except LayoutChanged:
                result["portals"][name] = "layout_changed"
            except Exception as e:  # jedna chyba portálu nesmí shodit ostatní
                result["portals"][name] = f"error: {type(e).__name__}: {e}"

    # 1) existující řádky: duplicity (stejný název+firma+místo) se archivují, nejstarší zůstává
    rows = notion.load_rows()
    keep, dups = split_duplicates(rows)
    result["duplicates_archived"] = 0
    for page_id in dups:
        if not dry_run:
            notion.archive(page_id)
        result["duplicates_archived"] += 1
    existing_urls = {row["url"] for row in rows}

    # 2) nové kandidáty: o každém rozhoduje jediná funkce decide() (detail se stahuje jen když je potřeba)
    result["write_errors"] = 0
    result["detail_unchecked"] = 0
    # --dry-run vyhodnotí vše znovu (ladění pravidel); ostrý běh přeskočí nedávno vyřazené (nestahuje detail znovu)
    recent_rejects = set() if dry_run else load_recent_rejects()
    reject_rows: list[list[str]] = []
    robots_cache: dict = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        for j in candidates.values():
            key = tuple(s.strip().lower() for s in (j.title, j.company, j.place))
            if j.url in existing_urls or key in keep:
                result["skipped_existing"] += 1
                continue
            if j.url in recent_rejects:
                result["skipped_rejected"] += 1
                continue
            d = decide(j, lambda: fetch_detail_text(client, j, robots_cache), profiles, blacklist)
            if not d.keep:
                result["excluded"][d.reason] = result["excluded"].get(d.reason, 0) + 1
                if d.reason not in TRANSIENT_REASONS:
                    reject_rows.append([date.today().isoformat(), j.portal, j.url, j.title, j.company, j.place,
                                        REASON_LABELS.get(d.reason, d.reason), d.note])
                continue
            if d.reason == "detail_neovereno":  # detail nešlo ověřit -> nabídka se uloží, ale počítá se
                result["detail_unchecked"] += 1
            j.category, j.score, j.source = d.category, d.score, d.source
            if not dry_run:
                try:
                    notion.create(j)
                except httpx.HTTPError:  # už zapsané nabídky se musí dostat do výsledku (a Telegramu)
                    result["write_errors"] += 1
                    continue
            if d.source == "detail":
                result["from_detail"] += 1
            keep[key] = None  # stejná pozice se v tomto běhu nezapíše dvakrát
            result["new"].append(j)
    if reject_rows or dry_run:
        write_reject_log(reject_rows, dry_run)
    return result
