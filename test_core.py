"""Offline testy (bez sítě): parsery na HTML fixturách + hlídání POST požadavků."""
import json
import re
from datetime import date
from pathlib import Path

import httpx
import pytest

import core

ROOT = Path(__file__).parent
FIX = ROOT / "tests" / "fixtures"
read = lambda n: (FIX / n).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _tmp_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "LOG_DIR", tmp_path / "logs")  # logs/vyrazene*.csv nikdy do repa


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
    job = core.Job("Tajný název", "Firma", "Pardubice", "jobs.cz", "https://www.jobs.cz/rpd/1/")
    sent = []
    monkeypatch.setattr(core, "run", lambda kws, **k: seen.setdefault("kws", kws) and
                        {"new": [job], "skipped_existing": 0, "portals": {"jobs.cz": "ok (1)"}})
    monkeypatch.setattr(core, "telegram_send", sent.append)
    telegram_bot.handle({"message": {"chat": {"id": 123}, "text": "/hledej python analytik"}}, "123")
    assert seen["kws"] == ["python", "analytik"]
    assert len(sent) == 2 and sent[1].startswith("<b>Scraping dokončen</b>") and "Nových nabídek (uloženo): 1" in sent[1]
    assert "Tajný název" not in sent[1] and "rpd/1" not in sent[1]  # jen počet, žádný výčet nabídek


def test_mcp_tool_returns_short_summary(monkeypatch):
    import mcp_server
    monkeypatch.setattr(core, "run", lambda kws, dry_run: {"new": [1, 2], "skipped_existing": 3,
                                                           "portals": {"jobs.cz": "ok (5)"}})
    out = mcp_server.run_scrape(["python"], True)
    assert "2" in out and "3" in out and "jobs.cz" in out


def test_place_ok():
    assert core.place_ok("Pardubice – Polabiny")
    assert core.place_ok("Slezské Předměstí, Hradec Králové")
    assert core.place_ok("Rychnov nad Kněžnou")
    assert core.place_ok("")  # prázdné místo se nevyřazuje
    assert core.place_ok("Holice") and core.place_ok("Býšť") and core.place_ok("Svítkov, Pardubice")  # 24. 11. okolí Pardubic
    for out in ("Lázně Bohdaneč", "Chrudim", "Havlíčkův Brod", "Kolín", "Středočeský kraj", "Celá ČR"):
        assert not core.place_ok(out)


def test_parse_cz_date_feb_29_in_common_year():
    assert core.parse_cz_date("29. února", date(2027, 3, 5)) is None  # dřív ValueError -> spadl celý portál


def test_split_duplicates_keeps_newest_and_worked_rows():
    row = lambda id, status, created: {"id": id, "key": ("a", "f", "m"), "status": status, "created": created}
    rows = [row("new2", "Nový", "2026-11-03"), row("new1", "Nový", "2026-11-01"),
            row("sent", "Odesláno", "2026-11-05"), row("sent2", "Odesláno", "2026-11-06")]
    keep, dups = core.split_duplicates(rows)
    assert keep[("a", "f", "m")]["id"] == "sent2"  # řádek, se kterým uživatel pracoval, má přednost
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

    def close(self):
        self.closed = True


def _run(monkeypatch, notion, dry_run, detail="", title="Analytik", posted=None):
    job = core.Job(title, "Firma", "Pardubice", "jobs.cz", "https://www.jobs.cz/rpd/1/", posted)
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.setenv("NOTION_DATABASE_ID", "d")
    monkeypatch.setattr(core, "load_dotenv", lambda: None)
    monkeypatch.setattr(core, "Notion", lambda *a: notion)
    monkeypatch.setattr(core, "search_portal", lambda c, n, k: ([job] if n == "jobs.cz" else [], 0))
    monkeypatch.setattr(core, "fetch_detail_text", lambda *a: detail)
    return core.run(["x"], dry_run=dry_run)


def test_cli_notify_escapes_and_survives_telegram_error(monkeypatch, capsys):
    import scrape
    res = {"new": [], "skipped_existing": 2, "excluded": {"ridicak": 1}, "manual_check": 0,
           "duplicates_archived": 0, "write_errors": 0,
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
    assert res["excluded"] == {"noc": 1} and res["new"] == [] and n.created == []


def test_vzv_in_detail_excluded_even_as_advantage(monkeypatch):
    n = _FakeNotion()
    res = _run(monkeypatch, n, False, detail="Požadujeme\nOprávnění VZV výhodou")
    assert res["excluded"] == {"vzv": 1} and res["new"] == [] and n.created == []


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


# ---------------------------------------------------------------- opravy z druhého auditu
def _row(id, url, portal="jobs.cz", status="Nový", created="2026-11-01"):
    return {"id": id, "url": url, "key": core._key("Analytik", "Firma", "Pardubice"),
            "created": created, "status": status}


def test_same_offer_on_other_portal_is_not_written_again(monkeypatch):
    n = _FakeNotion(rows=[_row("r1", "https://www.jenprace.cz/nabidka/1", portal="jenprace.cz")])
    res = _run(monkeypatch, n, False)  # kandidát je jobs.cz se stejným názvem, firmou, místem -> jeden řádek
    assert n.created == [] and res["skipped_existing"] == 1 and n.archived == []


def test_same_offer_on_same_portal_is_still_skipped(monkeypatch):
    n = _FakeNotion(rows=[_row("r1", "https://www.jobs.cz/rpd/99/")])  # jiné URL (repost), stejný portál
    res = _run(monkeypatch, n, False)
    assert n.created == [] and res["skipped_existing"] == 1


def test_split_duplicates_merges_across_portals():
    keep, dups = core.split_duplicates([_row("a", "u1", "jobs.cz"), _row("b", "u2", "jenprace.cz", created="2026-11-02")])
    assert dups == ["a"] and keep[("analytik", "firma", "pardubice")]["id"] == "b"  # starší pryč, novější zůstává, na portálu nezáleží


def test_load_rows_key_ignores_portal():
    page = {"id": "1", "created_time": "t", "properties": {
        core.COL_TITLE: {"title": [{"plain_text": "Analytik "}]}, core.COL_COMPANY: {"rich_text": [{"plain_text": "Firma"}]},
        core.COL_PLACE: {"rich_text": [{"plain_text": "Pardubice"}]}, core.COL_PORTAL: {"select": {"name": "jenprace.cz"}}}}
    n = core.Notion.__new__(core.Notion)
    n.c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": [page], "has_more": False})))
    n.ds_id = "ds"
    assert n.load_rows()[0]["key"] == ("analytik", "firma", "pardubice")


def test_run_archives_duplicates_but_dry_run_does_not(monkeypatch):
    rows = [_row("old", "u1"), _row("newer", "u2", created="2026-11-03"), _row("worked", "u3", status="Odesláno", created="2026-11-05")]
    n = _FakeNotion(rows=rows)
    res = _run(monkeypatch, n, False)
    assert sorted(n.archived) == ["newer", "old"] and res["duplicates_archived"] == 2  # řádek se změněným stavem má přednost a zůstává
    n = _FakeNotion(rows=rows)
    res = _run(monkeypatch, n, True)
    assert n.archived == [] and res["duplicates_archived"] == 2  # dry-run jen spočítá


def test_notion_client_is_closed_after_run(monkeypatch):
    n = _FakeNotion()
    _run(monkeypatch, n, False)
    assert n.closed
    n = _FakeNotion(fail=True)
    n.load_rows = lambda: 1 / 0  # i při chybě se klient zavře
    with pytest.raises(ZeroDivisionError):
        _run(monkeypatch, n, False)
    assert n.closed


def test_telegram_send_splits_long_text_without_losing_lines(monkeypatch):
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(core, "_send", lambda c, m, u, **kw: sent.append(kw["json"]["text"]) or httpx.Response(200))
    core.telegram_send(chr(10).join(f"řádek {i} " + "x" * 90 for i in range(200)))
    assert len(sent) > 1 and all(len(t) <= 4000 for t in sent)
    assert sum(t.count(chr(10)) for t in sent) == 200  # žádný řádek se neztratil ani nezdvojil


def test_stale_lock_is_ignored_and_removed(monkeypatch, tmp_path):
    import os
    import time
    lock = tmp_path / ".run.lock"
    lock.write_text("")
    old = time.time() - core.LOCK_MAX_AGE - 10
    os.utime(lock, (old, old))
    monkeypatch.setattr(core, "LOCK_FILE", lock)
    with core._run_lock():
        assert lock.exists()  # zámek převzal tento běh
    assert not lock.exists()


def test_newer_offer_replaces_older_row(monkeypatch):
    old = _row("old", "https://www.jenprace.cz/nabidka/1", portal="jenprace.cz", created="2026-11-01T08:00:00.000Z")
    n = _FakeNotion(rows=[old])
    res = _run(monkeypatch, n, False, posted=date(2026, 11, 10))  # inzerát novější než zápis -> nový se zapíše, starý pryč
    assert len(n.created) == 1 and n.archived == ["old"] and res["duplicates_archived"] == 1
    n = _FakeNotion(rows=[old])
    res = _run(monkeypatch, n, True, posted=date(2026, 11, 10))
    assert n.created == [] and n.archived == [] and res["duplicates_archived"] == 1 and len(res["new"]) == 1  # dry-run
    n = _FakeNotion(rows=[old])
    res = _run(monkeypatch, n, False, posted=date(2026, 11, 1))  # stejný den: není novější, jinak by se přepisovalo při každém běhu
    assert n.created == [] and n.archived == [] and res["skipped_existing"] == 1
    worked = dict(old, status="Odesláno")
    n = _FakeNotion(rows=[worked])
    _run(monkeypatch, n, False, posted=date(2026, 11, 10))  # řádek, se kterým uživatel pracoval, se nearchivuje a nepřepisuje
    assert n.created == [] and n.archived == []


def test_failed_write_does_not_archive_older_row(monkeypatch):
    n = _FakeNotion(fail=True, rows=[_row("old", "u", created="2026-11-01T08:00:00.000Z")])
    res = _run(monkeypatch, n, False, posted=date(2026, 11, 10))
    assert res["write_errors"] == 1 and n.archived == []  # starý řádek zůstane, když nový nejde zapsat


# ---------------------------------------------------------------- decide(): pravidla bez profilů (ruční hledání)
def _decide(title, text="", place="Pardubice", profiles=None, fetch=None, company="Firma"):
    j = core.Job(title, company, place, "jobs.cz", "https://www.jobs.cz/rpd/1/")
    return core.decide(j, fetch or (lambda: text), profiles, core.load_blacklist())


def test_decide_title_rules_without_profiles():
    """Ruční hledání (profiles=None): blacklist, lokalita a tvrdá pravidla, žádný whitelist."""
    ok = lambda t, **k: _decide(t, **k).keep
    why = lambda t, **k: _decide(t, **k).reason
    assert why("Recepční na ubytovně") == "blacklist"
    assert why("Finanční poradce - F2F") == "blacklist"
    assert why("Obchodník door to door") == "blacklist"
    assert why("Montér/opravář plynovodů") == "blacklist"
    assert why("Ženy a reality | Rychnov") == "blacklist"
    assert why("Prodavač/ka - Choceň", place="Choceň") == "lokalita"
    assert ok("Prodavač/ka - Pardubice")  # prodavač není na blacklistu
    assert ok("Pracovník/ice pro montáž sedaček")
    assert ok("Specialista prodeje - home office") and ok("Obchodní zástupce v B2B")
    assert why("Operátor výroby - noční směny") == "noc"
    assert why("Skladník, 12h denní i noční") == "noc"
    assert ok("Skladník, bez nočních, víkendy volné")
    assert why("MUDr. / lékařka na ambulanci") == "blacklist"
    assert why("Pracovník v sociálních službách") == "blacklist"
    assert why("Pečovatelka / pečující") == "blacklist"
    assert why("Asistent péče v domově pro seniory") == "blacklist"
    assert ok("Vánoční příprava skladu")  # "vánoční" není noční
    assert why("Skladník - řidič") == "blacklist"  # řidič platí vždy, whitelist "skladn" je zrušen
    assert why("Prodavač/pokladní") == "blacklist"
    assert why("Skladník - řidič VZV") == "blacklist"
    assert why("Skladník (bez nutnosti VZV)") == "vzv"
    assert why("Obsluha vysokozdvižného vozíku") == "vzv"
    assert why("Skladník - noční směny") == "noc"
    assert why("Kuchař / kuchařka") == "blacklist"
    assert why("Stavbyvedoucí senior") == "blacklist"
    assert why("Tech Lead Product Security") == "blacklist"
    assert why("Disponent MKD s ruštinou nebo ukrajinštinou") == "blacklist"
    assert why("Dokumentarista IPS") == "blacklist"


def test_hard_reason_driving_licence_only_as_requirement():
    h = core.hard_reason
    assert h("Něco\nŘidičský průkaz B\nDalší") == "ridicak"
    assert h("Řidičské oprávnění sk. B (aktivní řidič)") == "ridicak"
    assert h("Výhodou vám bude řidičský průkaz sk. B") is None  # od 24. 11. „výhodou“ projde
    assert h("Řidičský průkaz skupiny B - vítán") is None
    assert h("Řidičský průkaz sk. B není podmínkou") is None
    assert h("Výhody:\n- řidičský průkaz\n- angličtina") is None  # pod nadpisem „Výhody:“
    assert h("Požadujeme:\n- řidičský průkaz skupiny B") == "ridicak"
    assert h("Řidičský průkaz sk. B je nutností. Výhodou je angličtina.") == "ridicak"  # výhodou v jiné větě
    assert h("Výhodou je angličtina. Řidičský průkaz skupiny B je podmínkou.") == "ridicak"
    assert h("Výhodou: znalost AJ\nPožadujeme:\n- řidičský průkaz") == "ridicak"  # nejbližší nadpis je „Požadujeme:“
    assert h("Požadujeme řidičského průkazu skupiny B") == "ridicak"  # skloňování (dřív neodchyceno)
    assert h("Máte řidičák? Super") == "ridicak"
    assert h("Platné ŘP skupiny B") == "ridicak"
    assert h("Řidič / Řidička\nPrávo") is None  # samotné slovo řidič není oprávnění
    assert h("Bude vás zajímat naše řešení zpráv") is None  # "řp" uvnitř slova nesmí chytat
    from bs4 import BeautifulSoup
    html = "<ul><li>Řidičský průkaz sk. B <strong>výhodou</strong></li><li>Angličtina</li></ul>"
    assert h(BeautifulSoup(html, "html.parser").get_text("\n", strip=True)) == "ridicak"


def test_hard_reason_shifts():
    h = core.hard_reason
    assert h("Pracovní doba\nDvousměnný provoz včetně nočních směn") == "noc"
    assert h("Noční směny 22:00-6:00") == "noc"
    assert h("Pracovní doba: bez nočních směn") is None
    assert h("Noční směny nejsou") is None
    assert h("Pracovní doba\nPraktická pomoc, žádné noční") is None
    assert h("Ranní a odpolední směny") is None
    assert h("Benefity\nVánoční odměny a firemní večírek") is None
    assert h("Noční směny 22-6\nPráce bez nutnosti praxe") == "noc"  # zápor jen na stejném řádku
    assert h("Dvousměnný provoz, pondělí až pátek") is None  # dvousměnný je v pořádku
    assert h("Práce v nepřetržitém provozu") == "nepretrzity"
    assert h("Nepřetržitý provoz 24/7") == "nepretrzity"
    assert h("Non-stop provoz") == "nepretrzity"  # holé „non-stop“ bez provozu/směn už nestačí
    assert h("Pracujeme bez nepřetržitého provozu") is None
    assert h("Třísměnný provoz") == "trismenny"
    assert h("Práce na 3 směny") == "trismenny"
    assert h("Pracovní doba: tři směny týdně střídání") == "trismenny"


# ---------------------------------------------------------------- profese.json, whitelist, detail
PROFILES = core.load_profiles()


def _profiles_with_detail_discovery(tmp_dir):
    """Profily se zapnutými signály (v profese.json jsou přejmenované na `signaly_vypnuto`)."""
    data = json.loads(core.PROFILES_FILE.read_text(encoding="utf-8"))
    for c in data["kategorie"]:
        if "signaly_vypnuto" in c:
            c["signaly"] = c.pop("signaly_vypnuto")
    path = tmp_dir / "profese_detail.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return core.load_profiles(path)
CAT = {c["nazev"].split()[0]: c["nazev"] for c in PROFILES}  # "1" -> "1 Zákaznická podpora"


def test_profiles_and_blacklist_files_are_valid():
    assert [c["nazev"][0] for c in PROFILES] == ["1", "2", "3", "4", "5"]  # pořadí = priorita
    assert all(c["search"] and c["title"] for c in PROFILES)
    kws = core.search_keywords(PROFILES)
    assert len(kws) == len(set(kws)) and "zákaznická podpora" in kws and "montáž" in kws
    bl = core.load_blacklist()
    for w in ("kuchař", "recepční", "řidič", "finanční poradc", "montér", "pokladní"):
        assert w in bl
    assert not any(w.startswith("#") or w != w.lower() for w in bl)


def test_whitelist_title_match_gives_category_and_priority():
    d = lambda t, text="": _decide(t, text, profiles=PROFILES)
    cases = {
        "Specialista zákaznické péče": "1", "Technická podpora L1/L2": "1", "Specialista reklamací": "1",
        "Operátor/ka infolinky": "1", "Administrátor objednávek": "2", "Koordinátor servisu": "2",
        "Asistent prodeje": "2", "Administrativní pracovník": "2", "Referent back office": "2",
        "Zástupce vedoucího prodejny": "3", "Vedoucí prodejny": "3", "Vedoucí směny / úseku": "3",
        "Prodejce elektroniky": "4", "Prodejce u operátora O2": "4", "Inside sales manager": "4",
        "Skladník": "5", "Montážní pracovník": "5",
        "Specialista IT podpory": "1", "IT helpdesk L1": "1", "L1/L2 support": "1",
        "Customer Care Specialist": "1", "Klientský servis - příchozí hovory": "1", "Pracovník infocentra": "1",
        "Sekretářka": "2", "Specialista metodické podpory, oblast fakturace": "2",
        "Prodejce mobilních telefonů junior": "4", "Prodejce / prodejkyně ICT": "4",
        "Asistent nákupčího": "2", "Project Administrator": "2", "Projektový administrátor": "2",
    }
    for title, num in cases.items():
        r = d(title)
        assert r.keep and r.category == CAT[num] and r.source == "název", (title, r)
    assert d("Zákaznická podpora - montáž").category == CAT["1"]  # vyšší priorita vyhrává


def test_no_title_match_and_no_signals_is_rejected():
    r = _decide("Operátor výroby", "Obsluha stroje, balení, údržba", profiles=PROFILES)
    assert not r.keep and r.reason == "bez_shody"


def test_detail_discovery_is_off_in_shipped_profiles():
    """Nálezy z detailu daly v dry-runech jen šum (Foxconn, SERVISBAL), signály jsou vypnuté."""
    text = "Čeká vás práce s příchozími hovory a řešení reklamací zákazníků. Nabízíme benefity."
    r = _decide("Referent obsluhy klientů", text, profiles=PROFILES)
    assert not r.keep and r.reason == "bez_shody"
    assert not any(c["signaly"] for c in PROFILES)


def test_detail_discovery_needs_two_different_signals(tmp_path):
    PROFILES = _profiles_with_detail_discovery(tmp_path)
    text = "Čeká vás práce s příchozími hovory a řešení reklamací zákazníků. Nabízíme benefity."
    r = _decide("Referent obsluhy klientů", text, profiles=PROFILES)
    assert r.keep and r.source == "detail" and r.category == CAT["1"] and r.score >= 2
    r = _decide("Referent obsluhy klientů", "Řešení reklamací", profiles=PROFILES)  # jen jeden signál
    assert not r.keep and r.reason == "bez_shody"
    r = _decide("Referent obsluhy klientů", text + " Požadujeme řidičský průkaz.", profiles=PROFILES)
    assert not r.keep and r.reason == "ridicak"  # tvrdá pravidla platí i pro nalezené z detailu
    assert _decide("Kuchař - zákaznická linka", text, profiles=PROFILES).reason == "blacklist"


def test_category_5_is_never_found_from_detail():
    text = "montáž montáž sklad skladník skladové zásoby"
    assert not _decide("Referent", text, profiles=PROFILES).keep  # kat. 5 nemá signály


def test_hard_rules_apply_to_title_matches_in_detail():
    d = lambda text: _decide("Specialista zákaznické péče", text, profiles=PROFILES)
    assert d("Výhodou řidičský průkaz skupiny B").keep  # „výhodou“ projde
    assert d("Požadujeme řidičský průkaz skupiny B").reason == "ridicak"
    assert d("Noční směny").reason == "noc"
    assert d("Nepřetržitý provoz").reason == "nepretrzity"
    assert d("Třísměnný provoz").reason == "trismenny"
    assert d("Obsluha VZV").reason == "vzv"
    assert d("Dvousměnný provoz, pondělí až pátek").keep


def test_fakturant_only_basic_agenda():
    d = lambda text: _decide("Fakturant/ka", text, profiles=PROFILES)
    assert d("Vystavování faktur, kontrola plateb, komunikace s klienty").keep
    r = d("Samostatné vedení účetnictví a zpracování přiznání k DPH")
    assert not r.keep and r.reason == "pravidlo"
    assert _decide("Samostatná účetní", "vedení účetnictví", profiles=PROFILES).reason == "bez_shody"


def test_commission_ico_outbound_calls_drop_sales_and_call_centre():
    """Pravidlo převzaté z dřívějšího filtru: provize / IČO / odchozí hovory vyřadí prodej a call centrum."""
    d = lambda title, text: _decide(title, text, profiles=PROFILES)
    assert d("Prodejce elektroniky", "Provizní systém odměňování").reason == "pravidlo"
    assert d("Inside sales", "Spolupráce na IČO").reason == "pravidlo"
    assert d("Operátor/ka infolinky", "Příchozí i odchozí hovory").reason == "pravidlo"
    assert d("Prodejce elektroniky", "Fixní mzda, péče o stávající klienty").keep
    assert not d("Prodavač/ka", "Prémie a provize z prodeje").keep  # generický prodavač není v zadání (bez_shody)
    assert d("Vedoucí směny", "Prémie a provize z prodeje").keep  # retail management pod pravidlo nespadá


def test_decide_fetches_detail_at_most_once_and_only_when_needed():
    calls = []
    fetch = lambda: calls.append(1) or "Příchozí hovory, reklamace"
    _decide("Recepční", fetch=fetch, profiles=PROFILES)  # blacklist: detail netřeba
    _decide("Specialista", place="Kolín", fetch=fetch, profiles=PROFILES)  # lokalita: detail netřeba
    _decide("Noční operátor", fetch=fetch, profiles=PROFILES)  # tvrdé pravidlo v názvu
    assert calls == []
    _decide("Specialista zákaznické péče", fetch=fetch, profiles=PROFILES)
    assert calls == [1]


def test_unreachable_detail():
    none = lambda: None
    r = _decide("Vedoucí prodejny", fetch=none, profiles=PROFILES)
    assert r.keep and r.reason == "detail_neovereno" and r.category == CAT["3"]  # shoda v názvu se uloží
    r = _decide("Referent", fetch=none, profiles=PROFILES)
    assert not r.keep and r.reason == "detail_nedostupny"  # bez shody v názvu nelze rozhodnout (dočasné)
    assert _decide("Analytik", fetch=none).keep  # ruční hledání: jako dřív se uloží


# ---------------------------------------------------------------- běh s profily, logy, Notion sloupce
def _run_profiles(monkeypatch, jobs, details, notion, dry_run=False, keywords=None, searched=None):
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.setenv("NOTION_DATABASE_ID", "d")
    monkeypatch.setattr(core, "load_dotenv", lambda: None)
    monkeypatch.setattr(core, "Notion", lambda *a: notion)

    def search(c, n, k):
        if searched is not None:
            searched.append(list(k))
        return (jobs if n == "jobs.cz" else [], 0)
    monkeypatch.setattr(core, "search_portal", search)
    fetched = []
    monkeypatch.setattr(core, "fetch_detail_text", lambda c, j, r: fetched.append(j.url) or details.get(j.url, ""))
    res = core.run(keywords, dry_run=dry_run)
    res["fetched"] = fetched
    return res


def _jobs():
    mk = lambda i, t: core.Job(t, "Firma", "Pardubice", "jobs.cz", f"https://www.jobs.cz/rpd/{i}/")
    return [mk(1, "Specialista zákaznické péče"), mk(2, "Operátor výroby"), mk(3, "Vedoucí prodejny"),
            mk(4, "Referent klientů")]


def test_profile_run_uses_profile_keywords_and_whitelist(monkeypatch, tmp_path):
    detail_profiles = _profiles_with_detail_discovery(tmp_path)
    monkeypatch.setattr(core, "load_profiles", lambda *a: detail_profiles)
    details = {"https://www.jobs.cz/rpd/3/": "Požadujeme řidičský průkaz skupiny B",
               "https://www.jobs.cz/rpd/4/": "příchozí hovory, helpdesk a reklamace zákazníků"}
    n, searched = _FakeNotion(), []
    res = _run_profiles(monkeypatch, _jobs(), details, n, searched=searched)  # bez klíčových slov = profily
    assert searched[0] == core.search_keywords(PROFILES)
    assert [(j.title, j.source) for j in res["new"]] == [("Specialista zákaznické péče", "název"),
                                                         ("Referent klientů", "detail")]
    assert res["from_detail"] == 1
    assert res["excluded"] == {"bez_shody": 1, "ridicak": 1}
    assert [j.title for j in n.created] == ["Specialista zákaznické péče", "Referent klientů"]
    assert n.created[0].category == CAT["1"] and n.created[0].score >= core.TITLE_SCORE


def test_explicit_keywords_skip_whitelist(monkeypatch):
    n, searched = _FakeNotion(), []
    res = _run_profiles(monkeypatch, _jobs(), {}, n, keywords=["python"], searched=searched)
    assert searched[0] == ["python"] and len(res["new"]) == 4  # "Operátor výroby" projde: whitelist se neuplatní
    assert all(j.category == "" for j in res["new"])


def test_unverified_detail_is_saved_with_manual_label(monkeypatch):
    n = _FakeNotion()
    res = _run_profiles(monkeypatch, _jobs()[:1], {"https://www.jobs.cz/rpd/1/": None}, n)
    assert len(n.created) == 1 and n.notes == [core.MANUAL_LABEL] and res["manual_check"] == 1


def test_rejected_offers_are_logged_and_skipped_next_run(monkeypatch):
    first = _run_profiles(monkeypatch, _jobs(), {}, _FakeNotion())
    assert first["excluded"] == {"bez_shody": 2}
    rows = [r.split(";") for r in (core.LOG_DIR / "vyrazene.csv").read_text(encoding="utf-8-sig").splitlines()]
    assert rows[0][2] == "url" and {r[2] for r in rows[1:]} == {"https://www.jobs.cz/rpd/2/", "https://www.jobs.cz/rpd/4/"}
    assert all(r[6] == "bez shody s obory" for r in rows[1:])  # důvod je v logu čitelně
    second = _run_profiles(monkeypatch, _jobs(), {}, _FakeNotion(rows=[]))
    assert second["skipped_rejected"] == 2 and "https://www.jobs.cz/rpd/2/" not in second["fetched"]
    assert second["excluded"] == {}


def test_dry_run_logs_reasons_but_never_skips(monkeypatch):
    _run_profiles(monkeypatch, _jobs(), {}, _FakeNotion())  # ostrý běh zapíše skip-list
    res = _run_profiles(monkeypatch, _jobs(), {}, _FakeNotion(), dry_run=True)
    assert res["skipped_rejected"] == 0 and res["excluded"] == {"bez_shody": 2}  # dry-run vyhodnotí znovu
    assert (core.LOG_DIR / "vyrazene_dryrun.csv").exists()
    skip_before = (core.LOG_DIR / "vyrazene.csv").read_text(encoding="utf-8-sig")
    _run_profiles(monkeypatch, _jobs(), {}, _FakeNotion(), dry_run=True)
    assert (core.LOG_DIR / "vyrazene.csv").read_text(encoding="utf-8-sig") == skip_before  # dry-run skip-list nemění


def test_transient_detail_failure_is_not_logged(monkeypatch):
    j = [core.Job("Referent klientů", "F", "Pardubice", "jobs.cz", "https://www.jobs.cz/rpd/9/")]
    res = _run_profiles(monkeypatch, j, {"https://www.jobs.cz/rpd/9/": None}, _FakeNotion())
    assert res["excluded"] == {"detail_nedostupny": 1}
    assert not (core.LOG_DIR / "vyrazene.csv").exists()  # příště se vyhodnotí znovu


def test_notion_create_writes_optional_columns_only_when_present(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "_send", lambda c, m, u, **kw: sent.append(kw["json"]) or httpx.Response(200, request=httpx.Request(m, u)))
    n = core.Notion.__new__(core.Notion)
    n.c, n.ds_id, n.has_note = None, "ds", False
    j = core.Job("Vedoucí prodejny", "F", "Pardubice", "jobs.cz", "u", category="3 Retail management", score=12, source="název")
    n.optional = frozenset({core.COL_CATEGORY, core.COL_SCORE, core.COL_SOURCE})
    n.create(j)
    props = sent[-1]["properties"]
    assert props[core.COL_CATEGORY] == {"select": {"name": "3 Retail management"}}
    assert props[core.COL_SCORE] == {"number": 12} and props[core.COL_SOURCE] == {"select": {"name": "název"}}
    n.optional = frozenset()  # sloupce v databázi nejsou -> zápis nesmí spadnout na neexistující vlastnosti
    n.create(j)
    assert core.COL_CATEGORY not in sent[-1]["properties"] and core.COL_URL in sent[-1]["properties"]


def test_summary_lists_reasons_and_missing_columns():
    res = {"new": [1], "skipped_existing": 2, "excluded": {"ridicak": 5, "noc": 1}, "from_detail": 1,
           "skipped_rejected": 3, "missing_columns": ["Skóre"], "write_errors": 0, "portals": {"jobs.cz": "ok (9)"}}
    out = core.summary(res)
    assert "vyřazeno: 6 (řidičské oprávnění 5, noční směny 1)" in out
    assert "nalezeno z detailu" in out and "Skóre" in out and "nedávno vyřazeno): 3" in out



def test_decide_hard_rejection_note_contains_matched_text():
    job = core.Job("Specialista", "Firma", "Pardubice", "jobs.cz", "https://x/1")
    d = core.decide(job, lambda: "Požadujeme řidičský průkaz skupiny B.\nNabízíme benefity.", None, [])
    assert (d.keep, d.reason) == (False, "ridicak")
    assert "řidičský průkaz" in d.note
    d = core.decide(job, lambda: "Provoz firmy\nPracujeme v nepřetržitém provozu 24/7.", None, [])
    assert d.reason == "nepretrzity" and "24/7" in d.note


def test_noise_regressions_from_first_full_dry_run():
    """Nabídky, které první ostrý dry-run zbytečně uložil (Foxconn, revizní technik, generický prodavač)."""
    profiles = core.load_profiles()
    d = lambda title, text: core.decide(core.Job(title, "F", "Pardubice", "jobs.cz", "https://x/9"), lambda: text, profiles, [])
    assert not d("Technik výroby (m/ž) - herních konzolí", "Elektronika, tarifní mzda, výroba elektroniky").keep
    assert not d("Revizní technik | Technická podpora projektů a realizace", "Revize elektro").keep
    assert not d("Prodavač / prodavačka", "Prodej pečiva").keep
    assert d("Specialista technické podpory zákazníků", "Příchozí hovory, helpdesk").keep
    assert d("Prodavač/ka - vedoucí směny", "Řízení směny").keep


def test_it_support_only_l1_l2_not_expert():
    d = lambda title: _decide(title, "Běžná náplň práce", profiles=PROFILES)
    for title in ("Specialista IT podpory", "IT helpdesk L1", "Technik IT support L2", "L1 podpora uživatelů"):
        assert d(title).category == CAT["1"], title
    for title in ("Senior IT support specialist", "IT podpora L3 expert", "Vedoucí IT podpory", "IT architekt"):
        r = d(title)
        assert not r.keep or r.category != CAT["1"], (title, r)


def test_nonstop_needs_operation_context():
    h = core.hard_reason
    assert h("Hotline 24/7") is None  # služba firmy, ne směny (dry-run: motopůjčovna)
    assert h("možnost využití nonstop online lékařské poradny") is None  # benefit (dry-run: Kaufland)
    assert h("Pracujeme v nepřetržitém provozu") == "nepretrzity"
    assert h("Provoz 24/7") == "nepretrzity"
    assert h("Nonstop provoz, směny") == "nepretrzity"
    assert h("směny 24/7") == "nepretrzity"
    assert h("provoz 24 hodin denně") == "nepretrzity"


def test_home_office_passes_region_level_place():
    mk = lambda title, place: core.Job(title, "Firma", place, "jobs.cz", "https://x/7")
    d = lambda title, place: core.decide(mk(title, place), lambda: "Příchozí hovory", PROFILES, [])
    ho = "Klientský servis pro zákazníky Vodafone (příchozí hovory, plný home office)"
    assert d(ho, "Pardubický kraj").keep and d(ho, "kraj Vysočina").keep and d(ho, "Celá ČR").keep
    assert d(ho, "Pardubický kraj").category == CAT["1"]
    r = d("Klientský servis pro zákazníky Vodafone", "Pardubický kraj")  # bez home office se kraj nepouští
    assert not r.keep and r.reason == "lokalita"
    r = d(ho, "Praha")  # konkrétní město mimo lokalitu home office nezachrání
    assert not r.keep and r.reason == "lokalita"
