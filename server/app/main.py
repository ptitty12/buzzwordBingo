"""Application entrypoint.

Wires the routers together, serves the built frontend, and exposes the OpenAPI contract
at ``/api/docs`` — behind the admin PIN, so participants never wander into the integration
surface.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import get_connection
from .routers import admin, auth, ingest, meetings, stream, words
from .seed import run_seed

logger = logging.getLogger("jargon")

API_DESCRIPTION = """
Real-time, transcript-driven jargon tracking.

**How it works**

1. An admin curates the buzzword pool and opens a meeting.
2. Participants join a meeting with a nickname — no account — and draft a grid from the pool.
3. A transcription pipeline streams the meeting into `POST /api/ingest`, word by word.
4. The matching engine stems each token (`synergies` -> `synergy`, `leveraged` ->
   `leverage`) and marks every grid carrying that word.
5. Completed lines are ranked on the live standings, first to complete a line.

Participants can also propose new buzzwords via `POST /api/words/suggest`; an LLM curator
decides whether the term is jargon worth a square.

Clients subscribe to `ws://<host>/ws/meetings/{meeting_id}` for the live event stream.
"""

_basic = HTTPBasic(auto_error=False, description="Any username; password is the admin PIN.")


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
        logger.warning("ADMIN_PIN is empty — the admin console is disabled.")
    if not settings.moderation_enabled:
        logger.info(
            "ANTHROPIC_API_KEY unset — participant word suggestions will queue for admin review."
        )

    yield
    logger.info("shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description=API_DESCRIPTION,
        version="1.1.0",
        lifespan=lifespan,
        # Docs are mounted manually below so they can sit behind the admin PIN.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
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

    for router in (auth.router, words.router, meetings.router, ingest.router, admin.router):
        app.include_router(router)
    app.include_router(stream.router)

    _mount_docs(app)

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


def _require_docs_access(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    """Gate the API reference behind the admin PIN.

    HTTP Basic rather than a bearer token because these pages are opened directly in a
    browser tab, where the native credential prompt is the only workable challenge.
    """
    settings = get_settings()
    if not settings.protect_api_docs:
        return

    supplied = credentials.password if credentials else ""
    if not settings.admin_pin or not hmac.compare_digest(supplied, settings.admin_pin):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator PIN required.",
            headers={"WWW-Authenticate": 'Basic realm="Jargon Watch API"'},
        )


def _mount_docs(app: FastAPI) -> None:
    """Serve OpenAPI docs at the usual paths, guarded by :func:`_require_docs_access`."""

    @app.get("/api/openapi.json", include_in_schema=False)
    def openapi_schema(_: None = Depends(_require_docs_access)) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/api/docs", include_in_schema=False)
    def swagger_ui(_: None = Depends(_require_docs_access)):
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title=f"{app.title} — API")

    @app.get("/api/redoc", include_in_schema=False)
    def redoc(_: None = Depends(_require_docs_access)):
        return get_redoc_html(openapi_url="/api/openapi.json", title=f"{app.title} — API")


def _mount_frontend(app: FastAPI, static_dir: str) -> None:
    """Serve the built SPA when it exists, falling back to index.html for client routes."""
    dist = Path(static_dir)
    index = dist / "index.html"
    if not index.exists():

        @app.get("/", include_in_schema=False)
        def dev_placeholder() -> dict:
            return {
                "message": "Jargon Watch API is running.",
                "hint": "Run `npm run dev` for the frontend, or `npm run build` to serve it here.",
            }

        return

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        # An unmatched API or WebSocket path is a real 404, not a client route —
        # otherwise a typo'd endpoint silently returns the HTML shell with a 200 and
        # the caller gets "Unexpected token '<'" instead of a usable error.
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

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
