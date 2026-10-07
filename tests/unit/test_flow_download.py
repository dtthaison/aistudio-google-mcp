import httpx
import pytest
import respx

from aistudio._flow.download import download_signed
from aistudio.exceptions import ServerError, ValidationError

URL = "https://flow-content.google/video/m1?Expires=1&KeyName=k&Signature=s"


@respx.mock
async def test_downloads_a_signed_link_without_cookies():
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=b"mp4"))
    assert await download_signed(URL) == b"mp4"
    assert "cookie" not in {k.lower() for k in route.calls.last.request.headers}


async def test_other_hosts_are_refused():
    with pytest.raises(ValidationError):
        await download_signed("https://evil.example/video/m1")


@respx.mock
async def test_expired_links_and_redirects_are_errors():
    respx.get(URL).mock(return_value=httpx.Response(403))
    with pytest.raises(ServerError) as info:
        await download_signed(URL)
    assert "expired" in info.value.message
    respx.get(URL).mock(return_value=httpx.Response(302, headers={"location": "https://x.y/"}))
    with pytest.raises(ServerError):
        await download_signed(URL)
