"""Tenký MCP server: jediný nástroj run_scrape, který jen zavolá jádro."""
from mcp.server.mcpserver import MCPServer

import core

mcp = MCPServer("job-scraper")


@mcp.tool()
def run_scrape(keywords: list[str], dry_run: bool = True) -> str:
    """Vyhledá pracovní nabídky a uloží nové do Notionu (dry_run=True nic nezapisuje). Prázdný seznam slov = hledání podle profese.json. Vrací krátký souhrn."""
    try:
        res = core.run(keywords, dry_run=dry_run)
    except SystemExit as e:
        return f"Chyba: {e}"
    return core.summary(res, dry_run)


if __name__ == "__main__":
    mcp.run()
