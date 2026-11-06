"""Tenký MCP server: jediný nástroj run_scrape, který jen zavolá jádro."""
from mcp.server.mcpserver import MCPServer

import core

mcp = MCPServer("job-scraper")


@mcp.tool()
def run_scrape(keywords: list[str], dry_run: bool = True) -> str:
    """Vyhledá pracovní nabídky a uloží nové do Notionu (dry_run=True nic nezapisuje). Vrací krátký souhrn."""
    try:
        res = core.run(keywords, dry_run=dry_run)
    except SystemExit as e:
        return f"Chyba: {e}"
    portals = "; ".join(f"{n}: {s}" for n, s in res["portals"].items())
    verb = "by se uložilo" if dry_run else "uloženo"
    return f"Nových nabídek ({verb}): {len(res['new'])}, už existuje: {res['skipped_existing']}. Portály: {portals}"


if __name__ == "__main__":
    mcp.run()
