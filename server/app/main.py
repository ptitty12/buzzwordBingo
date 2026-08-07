"""Application entrypoint.

Wires the routers together, serves the built frontend, and exposes the OpenAPI contract
at ``/api/docs``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import get_connection
from .routers import admin, auth, games, ingest, stream, words
from .seed import run_seed

logger = logging.getLogger("bingo")

API_DESCRIPTION = """
Real-time, transcript-driven bingo.

**How it works**

1. An admin curates the buzzword pool and opens a game.
2. Players sign in with a nickname and draft a card from the pool.
3. A transcription pipeline streams the meeting into `POST /api/ingest`, word by word.
4. The matching engine stems each token (`synergies` -> `synergy`, `leveraged` ->
   `leverage`) and marks every card carrying that word.
5. Completed lines are ranked on the live leaderboard, first to bingo wins.

Clients subscribe to `ws://<host>/ws/games/{game_id}` for the live event stream.
"""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(name)-16s %(message)s",
        datefmt="%H:%M:%S",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    get_connection()
    result = run_seed()

    logger.info("%s starting  env=%s", settings.app_name, settings.environment)
    if result["words_added"]:
        logger.info("seeded %d buzzwords into the pool", result["words_added"])
    if result["api_key"]:
        logger.warning("INGEST API KEY (shown once): %s", result["api_key"])
    if not settings.admin_pin:
        logger.warning("ADMIN_PIN is unset — the admin console is open to anyone.")

    yield
    logger.info("shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description=API_DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def timing_header(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        return response

    for router in (auth.router, words.router, games.router, ingest.router, admin.router):
        app.include_router(router)
    app.include_router(stream.router)

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        """Liveness probe with a database round-trip."""
        row = get_connection().execute("SELECT COUNT(*) AS n FROM words").fetchone()
        return {
            "status": "ok",
            "environment": settings.environment,
            "words": row["n"] if row else 0,
            "version": app.version,
        }

    @app.exception_handler(500)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    _mount_frontend(app, settings.static_dir)
    return app


def _mount_frontend(app: FastAPI, static_dir: str) -> None:
    """Serve the built SPA when it exists, falling back to index.html for client routes."""
    dist = Path(static_dir)
    index = dist / "index.html"
    if not index.exists():
        @app.get("/", include_in_schema=False)
        def dev_placeholder() -> dict:
            return {
                "message": "Buzzword Bingo API is running.",
                "docs": "/api/docs",
                "hint": "Run `npm run dev` for the frontend, or `npm run build` to serve it here.",
            }
        return

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=not settings.is_production,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
