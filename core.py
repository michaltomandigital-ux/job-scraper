"""Scraper pracovních nabídek: hledá, parsuje, ukládá do Notionu.

NIKDY neodesílá reakce na inzeráty. POST jde jen na api.notion.com a api.telegram.org.
"""
import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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

# prace.cz: hledání podle slova je jen na /hledat/, které robots.txt zakazuje -> vždy robots_disallowed
PRACE_CZ = {
    "name": "prace.cz",
    "base": "https://www.prace.cz",
    "search": "https://www.prace.cz/hledat/?q={kw}&page={page}",
}

# Nesouvisející obory: nabídka, jejíž NÁZEV obsahuje některý z těchto výrazů (bez ohledu na velikost písmen),
# se do Notionu neukládá. Ostatní se uloží a filtr relevance je na uživateli v Notionu.
EXCLUDE_TITLE_WORDS = [
    "recepční", "číšník", "číšnic", "servírk", "pokladní", "pokladník",
    "uklízeč", "uklízečk",
    "brigádník", "kuchař", "řidič", "zdravotní sestra", "pečovatel",
]

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
        d = date(today.year, CZ_MONTHS[m.group(2)], int(m.group(1)))
        return d if d <= today else d.replace(year=today.year - 1)
    return None


def is_excluded(job: "Job") -> bool:
    """True, když název pozice patří do nesouvisejícího oboru (EXCLUDE_TITLE_WORDS)."""
    t = job.title.lower()
    return any(w in t for w in EXCLUDE_TITLE_WORDS)


def jsonld_jobs(soup: BeautifulSoup, portal: str) -> list[Job]:
    """Preferovaný zdroj: JSON-LD JobPosting (i vnořený v @graph / ItemList)."""
    out: list[Job] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                org = node.get("hiringOrganization") or {}
                loc = node.get("jobLocation") or {}
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                addr = loc.get("address") or {}
                place = addr.get("addressLocality") or addr.get("addressRegion") or ""
                posted = None
                if node.get("datePosted"):
                    try:
                        posted = datetime.fromisoformat(node["datePosted"][:10]).date()
                    except ValueError:
                        pass
                url = node.get("url") or ""
                if url and node.get("title"):
                    out.append(Job(node["title"], org.get("name", "") if isinstance(org, dict) else "",
                                   place, portal, url, posted))
            for v in node.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or ""))
        except (ValueError, TypeError):
            continue
    return out


# ---------------------------------------------------------------- portály
def parse_jobs_cz(html: str, today: date | None = None) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = jsonld_jobs(soup, JOBS_CZ["name"])
    if jobs:
        return jobs
    for card in soup.select(JOBS_CZ["card"]):
        a = card.select_one(JOBS_CZ["link"])
        if not a or not a.get("href"):
            continue
        href = urljoin(JOBS_CZ["base"], a["href"])
        m = re.search(r"/rpd/(\d+)", href)
        url = f"{JOBS_CZ['base']}/rpd/{m.group(1)}/" if m else href.split("?")[0]
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
    jobs = [j for j in jsonld_jobs(soup, cfg["name"])]
    if jobs:
        return jobs
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


def parse_prace_cz(html: str, today: date | None = None) -> list[Job]:
    return []  # nikdy se nevolá: /hledat/ je v robots.txt zakázané


# portál -> (konfigurace, parser). Další portály se přidají sem.
PORTALS = {
    "jobs.cz": (JOBS_CZ, parse_jobs_cz),
    "jenprace.cz": (JENPRACE_CZ, parse_jenprace_cz),
    "prace.cz": (PRACE_CZ, parse_prace_cz),
}


def robots_ok(client: httpx.Client, base: str, url: str) -> bool:
    rp = RobotFileParser()
    r = client.get(urljoin(base, "/robots.txt"))
    if r.status_code in (401, 403):
        return False
    rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    return rp.can_fetch(USER_AGENT, url)


def search_portal(client: httpx.Client, name: str, keywords: list[str]) -> list[Job]:
    cfg, parser = PORTALS[name]
    found: dict[str, Job] = {}
    first_request = True
    empty_first_page = False
    cutoff = date.today() - timedelta(days=MAX_POSTED_DAYS)
    for kw in keywords:
        for city in CITIES:
            for page in range(1, MAX_PAGES + 1):
                url = cfg["search"].format(city_slug=slugify(city), kw=quote_plus(kw), page=page,
                                           city_id=cfg.get("city_ids", {}).get(city, ""))
                if first_request:  # robots.txt se kontroluje jednou na portál, před hledáním
                    if not robots_ok(client, cfg["base"], url):
                        raise RobotsDisallowed(name)
                    first_request = False
                time.sleep(REQUEST_PAUSE)
                r = client.get(url)
                if r.status_code == 404:  # portál lokalitu nezná (např. jobs.cz Kostelec nad Orlicí) -> jen tato lokalita je prázdná
                    empty_first_page = empty_first_page or page == 1
                    break
                r.raise_for_status()
                jobs = parser(r.text)
                if not jobs and page == 1 and not found:
                    time.sleep(REQUEST_PAUSE)  # jeden opakovaný pokus (občasná prázdná stránka)
                    r = client.get(url)
                    r.raise_for_status()
                    jobs = parser(r.text)
                if not jobs:
                    empty_first_page = empty_first_page or page == 1
                    break
                new = [j for j in jobs if j.url not in found]
                all_dated_old = all(j.posted and j.posted < cutoff for j in jobs)
                for j in new:
                    if j.posted is None or j.posted >= cutoff:
                        found[j.url] = j
                if not new or all_dated_old:  # nové stránky už nic nepřinesou
                    break
    if not found and empty_first_page:  # HTTP 200, ale nula výsledků všude = podezření na změnu layoutu
        raise LayoutChanged(name)
    return list(found.values())


# ---------------------------------------------------------------- Notion
def _post(client: httpx.Client, url: str, **kw) -> httpx.Response:
    """Jediné místo, odkud se posílá POST. Povoleny jen api.notion.com a api.telegram.org."""
    if urlparse(url).hostname not in ALLOWED_POST_HOSTS:
        raise RuntimeError(f"POST na {url} je zakázán")
    for attempt in range(5):  # rate limit (429): počkat podle Retry-After a zkusit znovu
        r = client.post(url, **kw)
        if r.status_code != 429:
            break
        time.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
    return r


def _patch(client: httpx.Client, url: str, **kw) -> httpx.Response:
    """Úprava stránky Notionu (archivace duplicit). Povolen jen api.notion.com."""
    if urlparse(url).hostname != "api.notion.com":
        raise RuntimeError(f"PATCH na {url} je zakázán")
    for attempt in range(5):  # rate limit (429)
        r = client.patch(url, **kw)  # jediné místo, odkud se PATCH posílá
        if r.status_code != 429:
            break
        time.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
    return r


class Notion:
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
        props = self.c.get(f"/data_sources/{self.ds_id}").json().get("properties", {})
        self._check_schema(props)

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
            r = _post(self.c, f"https://api.notion.com/v1/data_sources/{self.ds_id}/query", json=body)
            r.raise_for_status()
            data = r.json()
            for p in data["results"]:
                props = p["properties"]
                text = lambda col: "".join(x.get("plain_text", "") for x in props.get(col, {}).get("title")
                                           or props.get(col, {}).get("rich_text") or [])
                key = tuple(s.strip().lower() for s in (text(COL_TITLE), text(COL_COMPANY), text(COL_PLACE)))
                rows.append({"id": p["id"], "url": props.get(COL_URL, {}).get("url") or "",
                             "key": key, "created": p.get("created_time", "")})
            if not data.get("has_more"):
                return rows
            cursor = data["next_cursor"]

    def archive(self, page_id: str):
        """Přesune řádek do koše Notionu (lze obnovit)."""
        r = _patch(self.c, f"https://api.notion.com/v1/pages/{page_id}", json={"archived": True})
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
        r = _post(self.c, "https://api.notion.com/v1/pages", json=body)
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
            r = _post(c, f"https://api.telegram.org/bot{token}/sendMessage",
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


# ---------------------------------------------------------------- hlavní běh
def run(keywords: list[str], dry_run: bool = False) -> dict:
    """Vrací {'new': [Job], 'skipped_existing': int, 'portals': {name: stav}}."""
    load_dotenv()
    token, db_id = os.getenv("NOTION_TOKEN"), os.getenv("NOTION_DATABASE_ID")
    if not token or not db_id:
        raise SystemExit("Chybí NOTION_TOKEN nebo NOTION_DATABASE_ID v .env")
    notion = Notion(token, db_id)  # kontrola schématu i ve --dry-run (jen čtení)

    result = {"new": [], "skipped_existing": 0, "excluded": 0, "portals": {}}
    candidates: dict[str, Job] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        for name in PORTALS:
            try:
                jobs = search_portal(client, name, keywords)
                result["portals"][name] = f"ok ({len(jobs)})"
                candidates.update({j.url: j for j in jobs})
            except RobotsDisallowed:
                result["portals"][name] = "robots_disallowed"
            except LayoutChanged:
                result["portals"][name] = "layout_changed"
            except Exception as e:  # jedna chyba portálu nesmí shodit ostatní
                result["portals"][name] = f"error: {type(e).__name__}: {e}"

    # 1) existující řádky: duplicity (stejný název+firma+místo) se archivují, nejstarší zůstává
    rows = notion.load_rows()
    keep, dups = {}, []
    for row in sorted(rows, key=lambda r: r["created"]):
        if row["key"] in keep:
            dups.append(row["id"])
        else:
            keep[row["key"]] = row
    result["duplicates_archived"] = 0
    for page_id in dups:
        if not dry_run:
            notion.archive(page_id)
        result["duplicates_archived"] += 1
    existing_urls = {row["url"] for row in rows}

    # 2) nové kandidáty: přeskočit, pokud už URL nebo stejná pozice (název+firma+místo) v Notionu je
    for j in candidates.values():
        if is_excluded(j):
            result["excluded"] += 1
            continue
        key = tuple(s.strip().lower() for s in (j.title, j.company, j.place))
        if j.url in existing_urls or key in keep:
            result["skipped_existing"] += 1
            continue
        if not dry_run:
            notion.create(j)
        keep[key] = None  # stejná pozice se v tomto běhu nezapíše dvakrát
        result["new"].append(j)
    return result
