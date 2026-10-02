"""PWA: манифест, икони и service worker.

Спецификацијата бараше „веб-апликација (PWA, за мобилен)". Тука се
проверува она што серверот го контролира: дали манифестот се служи, дали
service worker-от доаѓа од КОРЕНОТ со вистинското заглавие за опсег, и
дали страницата ги објавува.

Самата регистрација во прелистувач не се тестира тука - тоа бара вистински
прелистувач, а вградените панели често ја блокираат.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient


async def test_manifest_is_served(client: AsyncClient) -> None:
    response = await client.get("/static/manifest.webmanifest")
    assert response.status_code == 200
    assert "manifest" in response.headers["content-type"]


async def test_manifest_is_valid_and_installable(client: AsyncClient) -> None:
    """Прелистувачот бара име, start_url, display и барем една икона."""
    manifest = json.loads((await client.get("/static/manifest.webmanifest")).text)
    assert manifest["name"]
    assert manifest["short_name"]
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["icons"]


async def test_manifest_has_a_maskable_icon(client: AsyncClient) -> None:
    """Без maskable икона Android ја сече во квадрат со бела рамка."""
    manifest = json.loads((await client.get("/static/manifest.webmanifest")).text)
    purposes = {icon.get("purpose") for icon in manifest["icons"]}
    assert "maskable" in purposes


@pytest.mark.parametrize("path", ["/static/icon.svg", "/static/icon-maskable.svg"])
async def test_icons_are_served(client: AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]


async def test_service_worker_comes_from_the_root(client: AsyncClient) -> None:
    """Опсегот на service worker е ограничен на патеката од која доаѓа.

    Од /static/sw.js би покривал само /static/ - значи ништо корисно.
    """
    response = await client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


async def test_service_worker_declares_its_scope(client: AsyncClient) -> None:
    response = await client.get("/sw.js")
    assert response.headers.get("service-worker-allowed") == "/"


async def test_service_worker_is_not_cached(client: AsyncClient) -> None:
    """Кеширан service worker значи дека ажурирањата не стигнуваат."""
    response = await client.get("/sw.js")
    assert "no-cache" in response.headers.get("cache-control", "")


async def test_page_links_the_manifest_and_icons(client: AsyncClient) -> None:
    html = (await client.get("/")).text
    assert 'rel="manifest"' in html
    assert "/static/icon.svg" in html
    assert "apple-touch-icon" in html


async def test_page_registers_the_service_worker(client: AsyncClient) -> None:
    html = (await client.get("/")).text
    assert "serviceWorker" in html
    assert 'register("/sw.js")' in html


async def test_theme_colour_is_set_for_both_schemes(client: AsyncClient) -> None:
    html = (await client.get("/")).text
    assert html.count('name="theme-color"') == 2
