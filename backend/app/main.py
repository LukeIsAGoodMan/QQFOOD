import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import Base, engine

mimetypes.add_type("application/manifest+json", ".webmanifest")
from .agent import oauth as agent_oauth, server as agent_server
from .routers import brief as brief_r
from .routers import admin, aliases, auth, batches, evidence, ingest, inventory, metrics, notebook, orders, pages, production, purchasing, records, tasks

def _ensure_columns():
    """Tiny forward-only migration for columns added after v0.1 (create_all does not alter tables)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    adds = {"users": [("agent_enabled", "BOOLEAN NOT NULL DEFAULT 0")],
            "warehouses": [("tracked", "BOOLEAN NOT NULL DEFAULT 1"), ("active", "BOOLEAN NOT NULL DEFAULT 1")],
            "skus": [("catch_weight", "BOOLEAN NOT NULL DEFAULT 0"), ("purchase_pack", "FLOAT"), ("ship_pack", "FLOAT"),
                     ("active", "BOOLEAN NOT NULL DEFAULT 1"), ("merged_into", "VARCHAR(32)")],
            "production_batches": [("order_id", "VARCHAR(32)"), ("consumed_qty", "FLOAT"), ("loss_qty", "FLOAT"), ("yield_pct", "FLOAT"),
                                   ("expected_yield_pct", "FLOAT"), ("loss_reason", "VARCHAR(300) NOT NULL DEFAULT ''"),
                                   ("boxes", "INTEGER NOT NULL DEFAULT 0")],
            "customers": [("accounting_name", "VARCHAR(200) NOT NULL DEFAULT ''")],
            "purchase_orders": [("doc_type", "VARCHAR(16) NOT NULL DEFAULT 'invoice'"), ("exceptions", "JSON NOT NULL DEFAULT '[]'"),
                                ("quick", "BOOLEAN NOT NULL DEFAULT 0")]}
    with engine.begin() as conn:
        for table, cols in adds.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _load_org_mode():
    from .core import ORG
    from .db import SessionLocal
    from .models import AppSetting
    db = SessionLocal()
    try:
        s = db.get(AppSetting, "org")
        if s and (s.value or {}).get("mode") in ("simple", "standard"):
            ORG["mode"] = s.value["mode"]
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app):
    Base.metadata.create_all(engine)
    _ensure_columns()
    from .seed import seed_if_empty, upgrade_pages, upgrade_sources
    seed_if_empty()
    upgrade_pages()
    upgrade_sources()
    from .seed import upgrade_orders
    upgrade_orders()
    from .seed import upgrade_aliases
    upgrade_aliases()
    from .seed import upgrade_categories
    upgrade_categories()
    from .seed import upgrade_brief
    upgrade_brief()
    _load_org_mode()
    import asyncio
    import os
    from .brief import morning_loop
    task = None if os.environ.get("QQ_DISABLE_SCHEDULER") else asyncio.create_task(morning_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="QQ Food 597 运营管理系统", version="0.3.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

for r in (brief_r, auth, pages, records, tasks, evidence, inventory, aliases, purchasing, orders, production, batches, admin, notebook, ingest, metrics, agent_oauth, agent_server):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version}


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# Serve built frontend (npm run build) from the same origin in production.
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        f = DIST / full_path
        if full_path and f.is_file() and not full_path.startswith("api"):
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
