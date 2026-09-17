from __future__ import annotations

from typing import Any

import httpx


class SparqlError(RuntimeError):
    """Raised when GraphDB cannot execute a SPARQL request."""


class SparqlClient:
    def __init__(self, endpoint: str, *, timeout: float = 30.0) -> None:
        self.endpoint = endpoint
        self._client = httpx.AsyncClient(timeout=timeout)

    async def select(self, query: str) -> list[dict[str, dict[str, str]]]:
        try:
            response = await self._client.post(
                self.endpoint,
                content=query.encode("utf-8"),
                headers={
                    "Content-Type": "application/sparql-query; charset=utf-8",
                    "Accept": "application/sparql-results+json",
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            return payload.get("results", {}).get("bindings", [])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise SparqlError(f"GraphDB query failed: {exc}") from exc

    async def ask(self, query: str) -> bool:
        try:
            response = await self._client.post(
                self.endpoint,
                content=query.encode("utf-8"),
                headers={
                    "Content-Type": "application/sparql-query; charset=utf-8",
                    "Accept": "application/sparql-results+json",
                },
            )
            response.raise_for_status()
            return bool(response.json().get("boolean"))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise SparqlError(f"GraphDB ASK failed: {exc}") from exc

    async def update(self, query: str) -> None:
        try:
            response = await self._client.post(
                self.endpoint,
                content=query.encode("utf-8"),
                headers={"Content-Type": "application/sparql-update; charset=utf-8"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SparqlError(f"GraphDB update failed: {exc}") from exc

    async def close(self) -> None:
        await self._client.aclose()
