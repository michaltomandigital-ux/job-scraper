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
    assert not core.is_excluded(mk("Prodavač/ka - Choceň"))  # prodavač už není v seznamu
    assert not core.is_excluded(mk("Specialista prodeje - home office"))
    assert not core.is_excluded(mk("Obchodní zástupce v B2B"))


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
    assert not core.robots_ok(client, "https://x.cz", "https://x.cz/hledat/?q=a")
    assert core.robots_ok(client, "https://x.cz", "https://x.cz/nabidky/")


def test_prace_cz_is_skipped_by_robots():
    # skutečný robots.txt prace.cz zakazuje /hledat/ (kopie pravidla)
    robots = "User-Agent: *\nDisallow: /auth/\nDisallow: /hledat/\n"
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=robots)))
    url = core.PRACE_CZ["search"].format(kw="python", page=1)
    assert not core.robots_ok(client, core.PRACE_CZ["base"], url)


# ---------------------------------------------------------------- POST guard
def test_post_helper_blocks_foreign_hosts():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(RuntimeError):
        core._post(client, "https://www.jobs.cz/api/apply", json={})
    core._post(client, "https://api.notion.com/v1/pages", json={})
    core._post(client, "https://api.telegram.org/botX/sendMessage", json={})


def test_code_never_posts_outside_helper():
    """Ve zdrojácích smí být `.post(` jen v core._post; ostatní kód musí volat _post (s kontrolou hostitele)."""
    offenders = []
    for f in ROOT.glob("*.py"):
        if f.name == "test_core.py":
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.(post|put|patch|delete|request)\(", line) and "r = client.post(url, **kw)" not in line and "r = client.patch(url, **kw)" not in line:  # jediná místa v _post/_patch
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, offenders


def test_post_urls_only_allowed_hosts():
    """Každé URL předávané do _post() musí mířit na api.notion.com / api.telegram.org."""
    for f in ROOT.glob("*.py"):
        if f.name == "test_core.py":
            continue
        for m in re.finditer(r"_post\([^,]+,\s*f?\"(https?://[^/\"]+)", f.read_text(encoding="utf-8")):
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
