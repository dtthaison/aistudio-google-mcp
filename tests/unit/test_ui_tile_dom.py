"""PlaywrightUi against a real headless Chrome: Flow's grid DOM, and reply capture."""
import http.server
import threading

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


REPLY = ")]}'\n\n12\n[\"wrb.fr\",1]"


class _Server(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._send(b"<html><body>flow</body></html>", "text/html")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self._send(REPLY.encode(), "application/json")

    def _send(self, body, kind):
        self.send_response(200)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


async def test_fetched_replies_reach_both_aistudio_and_the_page(raw_page, local_site):
    # Live (2026-11-02): Chrome handed over StreamChat bodies empty on 3 of 4 runs; aistudio now
    # fetches those replies itself and gives the page the same response
    await raw_page.goto(local_site)
    async with PlaywrightUi(raw_page).responses(marker="StreamChat", fetch_bodies=True) as log:
        page_saw = await raw_page.evaluate(
            "() => fetch('/rpc/StreamChat?x=1', {method: 'POST', body: 'f.req=1'})"
            ".then(r => r.text())")
        captured = await log.next("StreamChat", timeout_s=5)
        assert await log.next("StreamChat", timeout_s=0.5) is None  # logged once
    assert page_saw == REPLY
    assert captured.status == 200 and captured.text == REPLY
    assert captured.request_body == "f.req=1"
