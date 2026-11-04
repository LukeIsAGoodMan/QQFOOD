#!/usr/bin/env bash
# Build the frontend (if needed) and serve everything from the backend on :8597.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r backend/requirements.txt; }
[ -d frontend/node_modules ] || (cd frontend && npm install)
(cd frontend && npm run build)
cd backend && exec ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8597}"
