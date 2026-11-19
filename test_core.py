"""Offline testy (bez sítě): parsery na HTML fixturách + hlídání POST požadavků."""
import re
from datetime import date
from pathlib import Path

import httpx
import pytest

import core

ROOT = Path(__file__).parent
FIX = ROOT / "tests" / "fixtures"
read = lambda n: (FIX / n).read_text(encoding="utf-8")


def test_is_excluded_by_title():
    mk = lambda t: core.Job(t, "f", "p", "jobs.cz", "https://www.jobs.cz/rpd/1/")
    assert core.is_excluded(mk("Recepční na ubytovně"))
    assert core.is_excluded(mk("Prodavač/ka - Choceň"))  # prodavač se vyřazuje natvrdo
    assert core.is_excluded(mk("Finanční poradce - F2F"))
    assert core.is_excluded(mk("Obchodník door to door"))
    assert not core.is_excluded(mk("Specialista prodeje - home office"))
    assert not core.is_excluded(mk("Obchodní zástupce v B2B"))
    assert core.is_excluded(mk("Operátor výroby - noční směny"))
    assert core.is_excluded(mk("Skladník, 12h denní i noční"))
    assert not core.is_excluded(mk("Skladník, bez nočních, víkendy volné"))
    assert core.is_excluded(mk("MUDr. / lékařka na ambulanci"))
    assert core.is_excluded(mk("Pracovník v sociálních službách"))
    assert core.is_excluded(mk("Pečovatelka / pečující"))
    assert core.is_excluded(mk("Asistent péče v domově pro seniory"))
    assert not core.is_excluded(mk("Vánoční příprava skladu"))  # "vánoční" není noční
    assert not core.is_excluded(mk("Skladník - řidič"))
    assert core.is_excluded(mk("Skladník - řidič VZV"))  # VZV vyřadí vždy, i chtěnou pozici
    assert core.is_excluded(mk("Skladník (bez nutnosti VZV)"))
    assert core.is_excluded(mk("Obsluha vysokozdvižného vozíku"))
    assert core.is_excluded(mk("Skladník - noční směny"))  # noční směny ale vyřadí i skladníka


def test_parse_cz_date():
    today = date(2026, 11, 4)
    assert core.parse_cz_date("Přidáno dnes", today) == today
    assert core.parse_cz_date("Přidáno včera", today) == date(2026, 11, 3)
    assert core.parse_cz_date("dnešní", today) == today
    assert core.parse_cz_date("včerejší", today) == date(2026, 11, 3)
    assert core.parse_cz_date("Zveřejněno: 30. října", today) == date(2026, 10, 30)
    assert core.parse_cz_date("22. prosince", today) == date(2025, 12, 22)  # budoucí datum -> loni
    assert core.parse_cz_date("nesmysl", today) is None


def test_jobs_cz_parser():
    jobs = core.parse_jobs_cz(read("jobs_cz_list.html"), today=date(2026, 11, 4))
    assert len(jobs) == 16
    j = jobs[0]
    assert j.portal == "jobs.cz"
    assert j.title and j.company and j.place
    assert re.fullmatch(r"https://www\.jobs\.cz/rpd/\d+/", j.url)  # bez ?searchId=
    assert j.posted == date(2026, 11, 3)


def test_jenprace_parser_skips_recommended_offers():
    jobs = core.parse_jenprace_cz(read("jenprace_list.html"), today=date(2026, 11, 4))
    assert len(jobs) == 1  # stránka má 99 článků, ale jen 1 je skutečná shoda
    j = jobs[0]
    assert j.portal == "jenprace.cz"
    assert j.url.startswith("https://www.jenprace.cz/nabidka/") and "?" not in j.url
    assert "km" not in j.place
    assert j.posted == date(2026, 11, 4)


def test_empty_page_gives_no_jobs():
    assert core.parse_jobs_cz("<html><body>nic</body></html>") == []
    assert core.parse_jenprace_cz("<html><body>nic</body></html>") == []


def test_robots_disallow_and_allow():
    robots = "User-agent: *\nDisallow: /hledat/\n"
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=robots)))
    rp = core.robots_parser(client, "https://x.cz")
    assert not rp.can_fetch(core.USER_AGENT, "https://x.cz/hledat/?q=a")
    assert rp.can_fetch(core.USER_AGENT, "https://x.cz/nabidky/")


# ---------------------------------------------------------------- POST guard
def test_send_helper_blocks_foreign_hosts():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(RuntimeError):
        core._send(client, "POST", "https://www.jobs.cz/api/apply", json={})
    with pytest.raises(RuntimeError):
        core._send(client, "PATCH", "https://api.telegram.org/botX/x", json={})  # PATCH jen Notion
    with pytest.raises(RuntimeError):
        core._send(client, "DELETE", "https://api.notion.com/v1/pages/x")
    core._send(client, "POST", "https://api.notion.com/v1/pages", json={})
    core._send(client, "POST", "https://api.telegram.org/botX/sendMessage", json={})
    core._send(client, "PATCH", "https://api.notion.com/v1/pages/x", json={})


def test_code_never_posts_outside_helper():
    """Ve zdrojácích smí být `.post(`/`.patch(`/`.request(` jen v core._send (s kontrolou hostitele)."""
    offenders = []
    for f in ROOT.glob("*.py"):
        if f.name == "test_core.py":
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.(post|put|patch|delete|request)\(", line) and "r = client.request(method, url, **kw)" not in line:  # jediné místo v _send
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, offenders


def test_send_urls_only_allowed_hosts():
    """Každé URL předávané do _send() musí mířit na api.notion.com / api.telegram.org."""
    for f in ROOT.glob("*.py"):
        if f.name == "test_core.py":
            continue
        for m in re.finditer(r"_send\([^,]+,\s*\"[A-Z]+\",\s*f?\"(https?://[^/\"]+)", f.read_text(encoding="utf-8")):
            assert m.group(1).split("//")[1] in core.ALLOWED_POST_HOSTS, (f.name, m.group(1))


# ---------------------------------------------------------------- Telegram / MCP
def test_notify_sends_nothing_without_new_jobs(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "telegram_configured", lambda: True)
    monkeypatch.setattr(core, "telegram_send", sent.append)
    assert core.notify_telegram([]) is False and not sent
    job = core.Job("Pozice", "Firma", "Pardubice", "jobs.cz", "https://www.jobs.cz/rpd/1/")
    assert core.notify_telegram([job]) is True
    assert "Pozice" in sent[0] and "Firma" in sent[0] and "Pardubice" in sent[0] and "rpd/1" in sent[0]


def test_notify_needs_tokens(monkeypatch):
    monkeypatch.setattr(core, "telegram_configured", lambda: False)
    job = core.Job("P", "F", "M", "jobs.cz", "u")
    assert core.notify_telegram([job]) is False


def test_bot_ignores_other_chats_and_other_commands(monkeypatch):
    import telegram_bot
    calls = []
    monkeypatch.setattr(core, "run", lambda *a, **k: calls.append("run"))
    monkeypatch.setattr(core, "telegram_send", lambda t: calls.append("send"))
    msg = lambda chat, text: {"message": {"chat": {"id": chat}, "text": text}}
    telegram_bot.handle(msg(999, "/hledej python"), "123")  # cizí chat
    telegram_bot.handle(msg(123, "ahoj"), "123")  # jiný text
    telegram_bot.handle(msg(123, "/start"), "123")  # jiný příkaz
    assert calls == []
    telegram_bot.handle(msg(123, "/hledej"), "123")  # bez slov -> jen nápověda, žádný scraping
    assert calls == ["send"]


def test_bot_runs_scrape_for_own_chat(monkeypatch):
    import telegram_bot
    seen = {}
    monkeypatch.setattr(core, "run", lambda kws, **k: seen.setdefault("kws", kws) and
                        {"new": [], "skipped_existing": 0, "portals": {"jobs.cz": "ok (0)"}})
    monkeypatch.setattr(core, "telegram_send", lambda t: None)
    telegram_bot.handle({"message": {"chat": {"id": 123}, "text": "/hledej python analytik"}}, "123")
    assert seen["kws"] == ["python", "analytik"]


def test_mcp_tool_returns_short_summary(monkeypatch):
    import mcp_server
    monkeypatch.setattr(core, "run", lambda kws, dry_run: {"new": [1, 2], "skipped_existing": 3,
                                                           "portals": {"jobs.cz": "ok (5)"}})
    out = mcp_server.run_scrape(["python"], True)
    assert "2" in out and "3" in out and "jobs.cz" in out


def test_notify_first_line_bold_and_escaped(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "telegram_configured", lambda: True)
    monkeypatch.setattr(core, "telegram_send", sent.append)
    core.notify_telegram([core.Job("A <b> & B", "f", "p", "jobs.cz", "https://www.jobs.cz/rpd/1/")])
    assert sent[0].startswith("<b>Nové pracovní nabídky: 1</b>")
    assert "A &lt;b&gt; &amp; B" in sent[0]


def test_place_ok():
    assert core.place_ok("Pardubice – Polabiny")
    assert core.place_ok("Slezské Předměstí, Hradec Králové")
    assert core.place_ok("Rychnov nad Kněžnou")
    assert core.place_ok("")  # prázdné místo se nevyřazuje
    for out in ("Lázně Bohdaneč", "Chrudim", "Havlíčkův Brod", "Kolín", "Středočeský kraj", "Celá ČR"):
        assert not core.place_ok(out)


def test_requires_driving_licence():
    assert core.requires_driving_licence("Něco\nŘidičský průkaz B\nDalší")
    assert core.requires_driving_licence("Řidičské oprávnění sk. B (aktivní řidič)")
    assert core.requires_driving_licence("Řidičské oprávnění\nSkupina B")
    assert not core.requires_driving_licence("Výhodou vám bude řidičský průkaz sk. B")
    assert not core.requires_driving_licence("Řidičský průkaz skupiny B - vítán")
    assert not core.requires_driving_licence("Řidič / Řidička\nPrávo")  # odkaz v menu portálu
    # "výhodou" v samostatném tagu skončí po get_text("\n") na dalším řádku
    from bs4 import BeautifulSoup
    html = "<ul><li>Řidičský průkaz sk. B <strong>výhodou</strong></li><li>Angličtina</li></ul>"
    assert not core.requires_driving_licence(BeautifulSoup(html, "html.parser").get_text("\n", strip=True))


def test_parse_cz_date_feb_29_in_common_year():
    assert core.parse_cz_date("29. února", date(2027, 3, 5)) is None  # dřív ValueError -> spadl celý portál


def test_split_duplicates_keeps_oldest_and_worked_rows():
    row = lambda id, status, created: {"id": id, "key": ("a", "f", "m"), "status": status, "created": created}
    rows = [row("new2", "Nový", "2026-11-03"), row("new1", "Nový", "2026-11-01"),
            row("sent", "Odesláno", "2026-11-05"), row("sent2", "Odesláno", "2026-11-06")]
    keep, dups = core.split_duplicates(rows)
    assert keep[("a", "f", "m")]["id"] == "sent"  # řádek, se kterým uživatel pracoval, má přednost
    assert sorted(dups) == ["new1", "new2"]  # jiný stav než "Nový" se nikdy nearchivuje


def test_bot_reports_failed_run(monkeypatch):
    import telegram_bot
    sent = []
    def boom(*a, **k):
        raise httpx.ConnectError("x")
    monkeypatch.setattr(core, "run", boom)
    monkeypatch.setattr(core, "telegram_send", sent.append)
    telegram_bot.handle({"message": {"chat": {"id": 123}, "text": "/hledej python"}}, "123")
    assert sent[-1] == "Hledání selhalo: ConnectError"


# ---------------------------------------------------------------- hledání a běh (mock sítě)
def _card(n, posted="Přidáno před 30 dny"):
    return (f'<article class="SearchResultCard"><a class="SearchResultCard__titleLink" href="/rpd/{n}/">T{n}</a>'
            f'<span class="SearchResultCard__status">{posted}</span></article>')


def _search(monkeypatch, handler):
    monkeypatch.setattr(core, "REQUEST_PAUSE", 0)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return core.search_portal(client, "jobs.cz", ["x"])


def test_old_offers_and_404_city_are_not_layout_changed(monkeypatch):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        if "kostelec" in req.url.path:
            return httpx.Response(404)
        return httpx.Response(200, text=f"<html>{_card(1, '1. ledna')}</html>")  # karty jsou, ale staré
    jobs, failed = _search(monkeypatch, handler)  # dřív LayoutChanged
    assert jobs == [] and failed == 0


def test_empty_200_everywhere_is_layout_changed(monkeypatch):
    handler = lambda req: httpx.Response(200, text="" if req.url.path == "/robots.txt" else "<html></html>")
    with pytest.raises(core.LayoutChanged):
        _search(monkeypatch, handler)


def test_one_failed_query_keeps_other_results(monkeypatch):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        if "pardubice" in req.url.path:
            return httpx.Response(503)
        return httpx.Response(200, text=f"<html>{_card(7, 'Přidáno dnes')}</html>")
    jobs, failed = _search(monkeypatch, handler)
    assert [j.url for j in jobs] == ["https://www.jobs.cz/rpd/7/"] and failed == 1


def test_robots_5xx_means_disallowed():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert not core.robots_parser(client, "https://x.cz").can_fetch(core.USER_AGENT, "https://x.cz/nabidky/")


def test_robots_checked_for_every_url(monkeypatch):
    asked = []
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /prace/pardubice/\n")
        asked.append(req.url.path)
        return httpx.Response(200, text=f"<html>{_card(7, 'Přidáno dnes')}</html>")
    _search(monkeypatch, handler)
    assert asked and not any("pardubice" in p for p in asked)  # zakázaná lokalita se nestáhne


def test_robots_disallow_all_raises(monkeypatch):
    handler = lambda req: httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    with pytest.raises(core.RobotsDisallowed):
        _search(monkeypatch, handler)


def test_jobs_cz_link_without_rpd_keeps_query():
    h = '<article class="SearchResultCard"><a class="SearchResultCard__titleLink" href="https://f.jobs.cz/d?id=5&r=detail">T</a></article>'
    assert core.parse_jobs_cz(h)[0].url == "https://f.jobs.cz/d?id=5&r=detail"


class _FakeNotion:
    def __init__(self, fail=False, rows=()):
        self.created, self.archived, self.fail, self.rows = [], [], fail, list(rows)

    def load_rows(self):
        return self.rows

    def create(self, j, note=""):
        if self.fail:
            raise httpx.ConnectError("x")
        self.created.append(j)
        self.notes = getattr(self, "notes", []) + [note]

    def archive(self, i):
        self.archived.append(i)


def _run(monkeypatch, notion, dry_run, detail="", title="Analytik"):
    job = core.Job(title, "Firma", "Pardubice", "jobs.cz", "https://www.jobs.cz/rpd/1/")
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.setenv("NOTION_DATABASE_ID", "d")
    monkeypatch.setattr(core, "load_dotenv", lambda: None)
    monkeypatch.setattr(core, "Notion", lambda *a: notion)
    monkeypatch.setattr(core, "search_portal", lambda c, n, k: ([job] if n == "jobs.cz" else [], 0))
    monkeypatch.setattr(core, "fetch_detail_text", lambda *a: detail)
    return core.run(["x"], dry_run=dry_run)


def test_has_night_shift():
    assert core.has_night_shift("Pracovní doba\nDvousměnný provoz včetně nočních směn")
    assert core.has_night_shift("Noční směny 22:00-6:00")
    assert not core.has_night_shift("Pracovní doba: bez nočních směn")
    assert not core.has_night_shift("Noční směny nejsou")
    assert not core.has_night_shift("Pracovní doba\nPraktická pomoc, žádné noční")
    assert not core.has_night_shift("Ranní a odpolední směny")
    assert not core.has_night_shift("Benefity\nVánoční odměny a firemní večírek")
    assert core.has_night_shift("Noční směny 22-6\nPráce bez nutnosti praxe")  # zápor jen na stejném řádku


def test_cli_notify_escapes_and_survives_telegram_error(monkeypatch, capsys):
    import scrape
    res = {"new": [], "skipped_existing": 2, "excluded": 1, "excluded_licence": 0, "excluded_night": 0, "excluded_vzv": 0,
           "licence_unchecked": 0, "duplicates_archived": 0, "write_errors": 0,
           "portals": {"jobs.cz": "error: HTTPStatusError: url '...?q=x&page=1' <Response>"}}
    sent = []
    monkeypatch.setattr(core, "run", lambda *a, **k: res)
    monkeypatch.setattr(core, "telegram_configured", lambda: True)
    monkeypatch.setattr(core, "telegram_send", sent.append)
    monkeypatch.setattr("sys.argv", ["scrape.py", "x", "--notify"])
    scrape.main()
    assert sent[0].startswith("<b>Scraping dokončen</b>")  # posílá se i při 0 nových
    assert "&amp;page=1" in sent[0] and "&lt;Response&gt;" in sent[0]
    def fail(t):
        raise RuntimeError("Telegram sendMessage selhal (400): bad")
    monkeypatch.setattr(core, "telegram_send", fail)
    scrape.main()  # selhání Telegramu nesmí shodit běh
    assert "oznámení selhalo" in capsys.readouterr().out


def test_night_shift_in_detail_excluded(monkeypatch):
    n = _FakeNotion()
    res = _run(monkeypatch, n, False, detail="Noční směny 22:00-6:00")
    assert res["excluded_night"] == 1 and res["new"] == [] and n.created == []


def test_vzv_in_detail_excluded_even_as_advantage(monkeypatch):
    n = _FakeNotion()
    res = _run(monkeypatch, n, False, detail="Požadujeme\nOprávnění VZV výhodou")
    assert res["excluded_vzv"] == 1 and res["new"] == [] and n.created == []


def test_dry_run_writes_nothing(monkeypatch):
    n = _FakeNotion()
    res = _run(monkeypatch, n, True)
    assert len(res["new"]) == 1 and n.created == [] and n.archived == []


def test_failed_notion_write_does_not_abort_run(monkeypatch):
    res = _run(monkeypatch, _FakeNotion(fail=True), False)
    assert res["write_errors"] == 1 and res["new"] == []


def test_bot_escapes_user_keywords(monkeypatch):
    import telegram_bot
    sent = []
    monkeypatch.setattr(core, "run", lambda *a, **k: {"new": [], "skipped_existing": 0, "portals": {}})
    monkeypatch.setattr(core, "telegram_send", sent.append)
    telegram_bot.handle({"message": {"chat": {"id": 123}, "text": "/hledej <python>"}}, "123")
    assert "&lt;python&gt;" in sent[0] and "<python>" not in sent[0]


def test_rejected_offer_is_not_written_again(monkeypatch):
    """Řádek ve stavu "Vyřazeno" zůstává v Notionu, takže se stejný inzerát znovu nezapíše (ani se změněným názvem)."""
    url = "https://www.jobs.cz/rpd/1/"
    for title in ("Analytik", "Analytik / Analytička"):  # stejný název (klíč) i změněný (jen URL)
        row = {"id": "r1", "url": url, "key": (title.lower(), "firma", "pardubice"),
               "created": "2026-11-05T08:00:00.000Z", "status": "Vyřazeno"}
        n = _FakeNotion(rows=[row])
        res = _run(monkeypatch, n, False)
        assert n.created == [] and res["new"] == [] and res["skipped_existing"] == 1 and n.archived == []


def test_needs_review_titles():
    mk = lambda t: core.Job(t, "f", "p", "jobs.cz", "https://www.jobs.cz/rpd/1/")
    assert core.needs_review(mk("Obchodní zástupce"))
    assert core.needs_review(mk("Telesales operátor"))
    assert core.needs_review(mk("Sales Specialist"))
    assert not core.needs_review(mk("Team leader zákaznické podpory"))
    assert not core.needs_review(mk("Salesforce administrátor"))


def test_classify_description():
    c = core.classify_description
    assert c("Péče o stávající klienty, příchozí hovory, reklamace") == "keep"
    assert c("Získávání nových klientů, akvizice, uzavírání obchodů, helpdesk podpora") == "drop"
    assert c("Příchozí hovory, reklamace, tickety, CRM. Provize z prodeje") == "drop"  # provize vždy vyřadí
    assert c("Práce na IČO, administrativa, reklamace, onboarding") == "drop"
    assert c("Odchozí hovory i příchozí hovory, administrativa, reklamace") == "drop"
    assert c("Akvizice nových zákazníků, reklamace") == "manual"  # remíza
    assert c("Pracujeme v příjemném kolektivu") == "manual"  # žádné signály


def test_review_title_run(monkeypatch):
    t = "Obchodní zástupce"
    n = _FakeNotion()
    res = _run(monkeypatch, n, False, title=t, detail="Provizní systém, nábor")
    assert res["excluded_sales"] == 1 and n.created == []
    n = _FakeNotion()
    res = _run(monkeypatch, n, False, title=t, detail="Reklamace, příchozí hovory")
    assert len(n.created) == 1 and n.notes == [""] and res["manual_check"] == 0
    n = _FakeNotion()
    res = _run(monkeypatch, n, False, title=t, detail="Nic zvláštního")
    assert n.notes == [core.MANUAL_LABEL] and res["manual_check"] == 1
    n = _FakeNotion()  # detail nešel stáhnout -> ponechat, ručně ověřit
    res = _run(monkeypatch, n, False, title=t, detail=None)
    assert n.notes == [core.MANUAL_LABEL] and res["manual_check"] == 1


# ---------------------------------------------------------------- opravy z auditu
def test_search_continues_when_page_is_all_repeats(monkeypatch):
    """Stránka 1 jen z už nalezených nabídek (překryv měst) nesmí zastavit čtení stránky 2."""
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        page = req.url.params.get("page")
        if "hradec" in req.url.path:
            return httpx.Response(200, text=f"<html>{_card(1, 'Přidáno dnes')}</html>" if page == "1" else "<html></html>")
        body = _card(1, "Přidáno dnes") if page == "1" else _card(2, "Přidáno dnes") if page == "2" else ""
        return httpx.Response(200, text=f"<html>{body}</html>")
    jobs, _ = _search(monkeypatch, handler)
    assert {j.url for j in jobs} == {"https://www.jobs.cz/rpd/1/", "https://www.jobs.cz/rpd/2/"}


def test_licence_heading_next_line_does_not_make_it_optional():
    assert core.requires_driving_licence("Požadujeme řidičský průkaz sk. B\nZaměstnanecké výhody\n13. plat")


def test_blank_company_and_place_are_not_deduplicated():
    row = lambda id: {"id": id, "key": ("skladník", "", ""), "status": "Nový", "created": id}
    keep, dups = core.split_duplicates([row("1"), row("2")])
    assert dups == [] and keep == {}


def test_write_error_reason_is_status_code_only(monkeypatch):
    def fail(j, note=""):
        raise httpx.HTTPStatusError("url s tokenem", request=httpx.Request("POST", "https://api.notion.com/v1/pages"),
                                    response=httpx.Response(400))
    n = _FakeNotion()
    n.create = fail
    res = _run(monkeypatch, n, False)
    assert res["write_errors"] == 1 and res["first_write_error"] == "400"
    assert "(první: 400)" in core.summary(res) and "token" not in core.summary(res)


def test_send_retries_after_429(monkeypatch):
    slept, calls = [], []
    monkeypatch.setattr(core.time, "sleep", slept.append)
    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "3"}) if len(calls) == 1 else httpx.Response(200)
    r = core._send(httpx.Client(transport=httpx.MockTransport(handler)), "POST", "https://api.notion.com/v1/pages")
    assert r.status_code == 200 and len(calls) == 2 and slept == [3.0]


def test_load_rows_follows_pagination():
    def handler(req):
        cursor = __import__("json").loads(req.content).get("start_cursor")
        page = lambda i: {"id": i, "created_time": "2026-11-0" + i, "properties": {core.COL_TITLE: {"title": [{"plain_text": "T" + i}]}}}
        if cursor is None:
            return httpx.Response(200, json={"results": [page("1")], "has_more": True, "next_cursor": "c2"})
        return httpx.Response(200, json={"results": [page("2")], "has_more": False})
    n = core.Notion.__new__(core.Notion)
    n.c, n.ds_id = httpx.Client(transport=httpx.MockTransport(handler)), "ds"
    assert [r["id"] for r in n.load_rows()] == ["1", "2"]


def test_second_live_run_is_refused_while_lock_is_held(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "LOCK_FILE", tmp_path / ".run.lock")
    monkeypatch.setattr(core, "_run", lambda kws, dry: {"ok": True})
    with core._run_lock():
        with pytest.raises(SystemExit):
            core.run(["x"])
        assert core.run(["x"], dry_run=True) == {"ok": True}  # dry-run zámek nepotřebuje
    assert core.run(["x"]) == {"ok": True} and not core.LOCK_FILE.exists()
