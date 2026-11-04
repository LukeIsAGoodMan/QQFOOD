"""Agent transports (PRD §9.11):
- /mcp            MCP Streamable HTTP (JSON-RPC over POST, JSON responses) for ChatGPT connectors / Claude.
- /agent/v1/<tool> + /agent/openapi.json   for Custom GPT Actions (same tools, same OAuth).
Plus the in-app management API (connections, kill switch) and the original-file upload link.
"""
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from ..core import audit, current_user
from ..db import get_db
from ..models import AgentToken, BusinessRecord, Task, Transfer, User
from ..routers.evidence import create_link, ingest_file
from . import tools as T
from .oauth import SCOPES, agent_auth, public_base

router = APIRouter(tags=["agent"])

PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
RATE = 120  # calls per minute per connection
_calls: dict[str, deque] = defaultdict(deque)


def _rate_limit(token_id: str):
    q, now = _calls[token_id], time.time()
    while q and q[0] < now - 60:
        q.popleft()
    if len(q) >= RATE:
        raise HTTPException(429, "调用过于频繁，请稍后再试")
    q.append(now)


def _summ(args: dict) -> dict:
    return {k: (v[:200] if isinstance(v, str) else v) for k, v in (args or {}).items()}


def call_tool(ctx, db: Session, name: str, args: dict) -> tuple[dict | None, str | None]:
    _rate_limit(ctx.token.id)
    try:
        result = T.run(ctx, db, name, args)
        audit(db, ctx.user, "agent.call", "agent", ctx.token.id, tool=name, client=ctx.client, args=_summ(args), ok=True)
        db.commit()
        return result, None
    except T.ToolError as e:
        db.rollback()
        audit(db, ctx.user, "agent.call", "agent", ctx.token.id, tool=name, client=ctx.client, args=_summ(args), ok=False, error=str(e))
        db.commit()
        return None, str(e)


def _annotations(t):
    ro = t["tier"] == "read"
    return {"title": t["title"], "readOnlyHint": ro, "destructiveHint": False, "idempotentHint": ro, "openWorldHint": False}


# ---------- MCP ----------
def _rpc_result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _handle(ctx, db, msg: dict):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification (e.g. notifications/initialized)
        return None
    if method == "initialize":
        req = params.get("protocolVersion")
        return _rpc_result(mid, {"protocolVersion": req if req in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[1],
                                 "capabilities": {"tools": {"listChanged": False}},
                                 "serverInfo": {"name": "qq597", "title": "QQ Food 597 运营系统", "version": "0.2.0"},
                                 "instructions": T.INSTRUCTIONS})
    if method == "ping":
        return _rpc_result(mid, {})
    if method == "tools/list":
        allowed = [t for t in T.tool_list() if t["tier"] == "read" or "agent.write" in ctx.scopes]
        return _rpc_result(mid, {"tools": [{"name": t["name"], "title": t["title"], "description": t["description"],
                                            "inputSchema": t["schema"], "annotations": _annotations(t)} for t in allowed]})
    if method == "tools/call":
        result, err = call_tool(ctx, db, params.get("name", ""), params.get("arguments") or {})
        if err:
            return _rpc_result(mid, {"content": [{"type": "text", "text": f"错误：{err}"}], "isError": True})
        text = json.dumps(result, ensure_ascii=False, default=str)
        return _rpc_result(mid, {"content": [{"type": "text", "text": text}],
                                 "structuredContent": json.loads(text), "isError": False})
    if method in ("resources/list", "prompts/list"):
        return _rpc_result(mid, {method.split("/")[0]: []})
    return _rpc_error(mid, -32601, f"Method not found: {method}")


@router.post("/mcp")
async def mcp_post(request: Request, db: Session = Depends(get_db)):
    ctx = agent_auth(request, db)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), 400)
    msgs = body if isinstance(body, list) else [body]
    out = [r for r in (_handle(ctx, db, m) for m in msgs) if r is not None]
    if not out:
        return Response(status_code=202)
    return JSONResponse(out if isinstance(body, list) else out[0])


@router.get("/mcp")
def mcp_get():
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
def mcp_delete():
    return Response(status_code=204)


# ---------- GPT Actions (OpenAPI) ----------
@router.post("/agent/v1/{name}")
async def action_call(name: str, request: Request, db: Session = Depends(get_db)):
    ctx = agent_auth(request, db)
    try:
        args = await request.json()
    except Exception:
        args = {}
    result, err = call_tool(ctx, db, name, args if isinstance(args, dict) else {})
    if err:
        return JSONResponse({"error": err}, 400)
    return JSONResponse(json.loads(json.dumps(result, ensure_ascii=False, default=str)))


@router.get("/agent/openapi.json")
def openapi(request: Request):
    base = public_base(request)
    paths = {}
    for t in T.tool_list():
        paths[f"/agent/v1/{t['name']}"] = {"post": {
            "operationId": t["name"], "summary": t["title"], "description": t["description"][:300],
            "x-openai-isConsequential": t["tier"] != "read",
            "requestBody": {"required": True, "content": {"application/json": {"schema": t["schema"]}}},
            "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}},
                          "400": {"description": "业务规则拒绝或参数错误", "content": {"application/json": {"schema": {"type": "object"}}}}}}}
    return {"openapi": "3.1.0", "info": {"title": "QQ Food 597 运营系统", "version": "0.2.0", "description": T.INSTRUCTIONS[:300]},
            "servers": [{"url": base}], "paths": paths,
            "components": {"schemas": {}, "securitySchemes": {"oauth": {"type": "oauth2", "flows": {"authorizationCode": {
                "authorizationUrl": f"{base}/oauth/authorize", "tokenUrl": f"{base}/oauth/token", "scopes": SCOPES}}}}},
            "security": [{"oauth": list(SCOPES)}]}


@router.get("/agent/instructions")
def instructions():
    return {"instructions": T.INSTRUCTIONS}


# ---------- in-app management ----------
@router.get("/api/agent/status")
def status(request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    base = public_base(request)
    toks = db.query(AgentToken).filter_by(user_id=u.id, revoked=False).order_by(AgentToken.created_at.desc()).all()
    now = datetime.now(timezone.utc)
    live = {}
    for t in toks:  # one row per client (refresh rotation creates new rows)
        exp = t.refresh_expires_at.replace(tzinfo=timezone.utc) if t.refresh_expires_at and t.refresh_expires_at.tzinfo is None else t.refresh_expires_at
        if exp and exp > now and t.client_id not in live:
            live[t.client_id] = {"client_id": t.client_id, "client": t.client_name, "scope": t.scope, "connected_at": t.created_at,
                                 "last_used_at": t.last_used_at}
    return {"enabled": u.agent_enabled, "mcp_url": f"{base}/mcp", "openapi_url": f"{base}/agent/openapi.json",
            "authorize_url": f"{base}/oauth/authorize", "token_url": f"{base}/oauth/token", "instructions": T.INSTRUCTIONS,
            "public": not base.startswith(("http://127.", "http://localhost")), "connections": list(live.values()),
            "tools": [{"name": t["name"], "title": t["title"], "tier": t["tier"]} for t in T.tool_list()]}


@router.post("/api/agent/enable")
def enable(body: dict, u: User = Depends(current_user), db: Session = Depends(get_db)):
    on = bool(body.get("enabled"))
    u.agent_enabled = on
    if not on:  # kill switch: revoke everything immediately
        db.query(AgentToken).filter_by(user_id=u.id, revoked=False).update({"revoked": True})
    audit(db, u, "agent.enabled" if on else "agent.disabled", "user", u.id)
    db.commit()
    return {"enabled": on}


@router.post("/api/agent/connections/{client_id}/revoke")
def revoke_connection(client_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    n = db.query(AgentToken).filter_by(user_id=u.id, client_id=client_id, revoked=False).update({"revoked": True})
    audit(db, u, "agent.revoked", "oauth_client", client_id, tokens=n)
    db.commit()
    return {"revoked": n}


# ---------- original-file upload link (opened from the assistant on the phone) ----------
_uploads: dict[str, int] = defaultdict(int)


def _target_title(db, d):
    model = {"task": Task, "record": BusinessRecord, "transfer": Transfer}[d["t"]]
    obj = db.get(model, d["i"])
    if not obj:
        raise HTTPException(404, "目标不存在")
    return getattr(obj, "title", None) or getattr(obj, "code", "")


@router.get("/api/upload-link/{token}")
def upload_info(token: str, db: Session = Depends(get_db)):
    d = T.verify_upload(token)
    u = db.get(User, d["u"])
    if not u or not u.active:
        raise HTTPException(403, "上传链接无效")
    return {"target_type": d["t"], "target_title": _target_title(db, d), "purpose": d["p"], "user": u.display_name,
            "expires_at": datetime.fromtimestamp(d["e"], timezone.utc), "used": _uploads[token[-24:]]}


@router.post("/api/upload-link/{token}")
async def upload_by_link(token: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    d = T.verify_upload(token)
    u = db.get(User, d["u"])
    if not u or not u.active:
        raise HTTPException(403, "上传链接无效")
    _target_title(db, d)
    if _uploads[token[-24:]] >= 20:
        raise HTTPException(429, "此链接上传次数已达上限（20）")
    ev, ver, dedup = ingest_file(db, u, await file.read(), file.filename or "upload", source="upload-link")
    link = create_link(db, u, ver, d["t"], d["i"], d["p"])
    audit(db, u, "evidence.uploaded_via_link", d["t"], d["i"], evidence=ev.id, purpose=d["p"])
    db.commit()
    _uploads[token[-24:]] += 1
    return {"evidence_id": ev.id, "link_id": link.id, "dedup": dedup}
