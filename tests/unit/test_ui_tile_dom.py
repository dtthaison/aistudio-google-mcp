"""dispatch_click_near against real DOM shapes of Flow's media grid (headless Chromium)."""
import pytest

from aistudio._browser.ui import PlaywrightUi

playwright_api = pytest.importorskip("playwright.async_api")

TILE = """<div class="tile" data-id="{id}">
  <button aria-label="More options" onclick="window.hit='{id}'">⋮</button>
  {footer}
</div>"""
OLD_FOOTER = '<div class="footer"><span>{title}</span></div>'
# 2026-11: the caption sits inside a button labelled with the title (live)
NEW_FOOTER = '<div class="footer"><button aria-label="{title}"><span>{title}</span></button></div>'


@pytest.fixture
async def raw_page():
    async with playwright_api.async_playwright() as p:
        browser = None
        for channel in ("chrome", None):  # the CLI's own Chrome, else Playwright's Chromium
            try:
                browser = await p.chromium.launch(headless=True, channel=channel)
                break
            except Exception as exc:  # not installed on this machine
                reason = exc
        if browser is None:
            pytest.skip(f"no headless Chrome or Chromium: {reason}")
        page = await browser.new_page()
        yield page
        await browser.close()


def grid(footer: str) -> str:
    return "".join(TILE.format(id=i, footer=footer.format(title=t))
                   for i, t in (("a", "Apple on plate"), ("b", "Empty gym")))


@pytest.mark.parametrize("footer", [OLD_FOOTER, NEW_FOOTER], ids=["old", "captioned-button"])
async def test_dispatch_click_near_reaches_the_tiles_own_menu(raw_page, footer):
    await raw_page.set_content(grid(footer))
    await PlaywrightUi(raw_page).dispatch_click_near("button", "More options", "Empty gym")
    assert await raw_page.evaluate("window.hit") == "b"
