"""Скелетот се крева и одговара."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_index_renders(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "Marketchecker" in response.text


async def test_openapi_available(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Marketchecker"
