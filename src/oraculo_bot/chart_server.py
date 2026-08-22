from __future__ import annotations

import time
import uuid

from aiohttp import web

_TTL_SECONDS = 3600


class ChartServer:
    def __init__(self, port: int) -> None:
        self.port = port
        self._store: dict[str, tuple[str, float]] = {}
        self._app = web.Application()
        self._app.router.add_get("/chart/{token}", self._serve)
        self._runner: web.AppRunner | None = None

    def store_chart(self, html: str) -> str:
        self._evict_expired()
        token = uuid.uuid4().hex
        self._store[token] = (html, time.monotonic() + _TTL_SECONDS)
        return token

    def _evict_expired(self) -> None:
        now = time.monotonic()
        for k in [k for k, (_, exp) in self._store.items() if exp < now]:
            del self._store[k]

    async def _serve(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        entry = self._store.get(token)
        if entry is None or time.monotonic() > entry[1]:
            self._store.pop(token, None)
            raise web.HTTPNotFound(reason="Chart not found or expired.")
        return web.Response(text=entry[0], content_type="text/html")

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
