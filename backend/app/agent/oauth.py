"""OAuth 2.1 authorization server for external AI assistants (PRD §9.11 AGT).

- ChatGPT / Claude MCP connectors: dynamic client registration (RFC 7591) + PKCE (S256), public clients.
- Custom GPT Actions: admin-created confidential client (client_id/secret), callback under chatgpt.com/aip/.
- Tokens are opaque, stored hashed, scoped (agent.read / agent.write), revocable per connection.
- The user signs in with their own system account; the assistant can never do more than that user can.
"""
import base64
import hashlib
import html
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..core import audit, check_secret, login_failed, login_guard
from ..db import get_db
from ..models import AgentToken, OAuthClient, OAuthCode, User

router = APIRouter(tags=["agent-oauth"])

SCOPES = {"agent.read": "查看总览、任务、记录、库存、追溯、待核验项", "agent.write": "评论、上报异常、更新自己任务进度、提出待确认变更、生成原件上传链接"}
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
AGENT_GLOBAL = os.environ.get("QQ_AGENT_ENABLED", "1") != "0"
# Roles that may connect an assistant (sys_admin excluded: no business data through agents)
AGENT_ROLES = {"boss", "ops_manager", "purchaser", "warehouse", "production", "quality", "hr_finance", "staff", "page_admin"}


def now():
    return datetime.now(timezone.utc)


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def aware(d: datetime | None):
    return d.replace(tzinfo=timezone.utc) if d and d.tzinfo is None else d


def public_base(request: Request) -> str:
    env = os.environ.get("QQ_PUBLIC_URL")
    if env:
        return env.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


# ---------- discovery ----------
@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(request: Request):
    base = public_base(request)
    return {"resource": f"{base}/mcp", "authorization_servers": [base], "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"], "resource_name": "QQ Food 597 运营系统"}


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
def auth_server_metadata(request: Request):
    base = public_base(request)
    return {"issuer": base, "authorization_endpoint": f"{base}/oauth/authorize", "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register", "revocation_endpoint": f"{base}/oauth/revoke",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "scopes_supported": list(SCOPES),
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"]}


# ---------- dynamic client registration ----------
@router.post("/oauth/register")
async def register(request: Request, db: Session = Depends(get_db)):
    if not AGENT_GLOBAL:
        raise HTTPException(403, "agent interface disabled")
    body = await request.json()
    uris = body.get("redirect_uris") or []
    if not uris or not all(isinstance(u, str) and (u.startswith("https://") or u.startswith("http://localhost") or u.startswith("http://127.0.0.1")) for u in uris):
        return JSONResponse({"error": "invalid_redirect_uri"}, 400)
    c = OAuthClient(id="qqc_" + secrets.token_urlsafe(16), name=str(body.get("client_name") or "AI 助手")[:200],
                    redirect_uris=uris, created_via="dcr")
    db.add(c)
    db.commit()
    return JSONResponse({"client_id": c.id, "client_name": c.name, "redirect_uris": uris,
                         "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                         "response_types": ["code"]}, 201)


def create_admin_client(db: Session, name: str, prefixes: list[str]) -> tuple[str, str]:
    """For Custom GPT Actions: confidential client whose callback lives under chatgpt.com/aip/<gpt-id>/."""
    secret = secrets.token_urlsafe(32)
    c = OAuthClient(id="qqc_" + secrets.token_urlsafe(16), name=name, secret_hash=h(secret), redirect_prefixes=prefixes,
                    created_via="admin")
    db.add(c)
    db.commit()
    return c.id, secret


def redirect_ok(c: OAuthClient, uri: str) -> bool:
    if uri in (c.redirect_uris or []):
        return True
    return any(uri.startswith(p) for p in (c.redirect_prefixes or []))


# ---------- authorize (mobile-friendly login + consent page) ----------
PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>授权 AI 助手 · QQ 597</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f1f5f9;margin:0;padding:16px;color:#1e293b}}
.card{{max-width:420px;margin:24px auto;background:#fff;border-radius:16px;padding:20px;box-shadow:0 1px 3px #0002}}
h1{{font-size:18px;margin:0 0 4px}} .muted{{color:#64748b;font-size:13px}} label{{display:block;margin:12px 0 4px;font-size:13px;color:#475569}}
input[type=text],input[type=password]{{width:100%;box-sizing:border-box;font-size:16px;padding:12px;border:1px solid #cbd5e1;border-radius:10px}}
.scope{{display:flex;gap:10px;align-items:flex-start;padding:10px;border:1px solid #e2e8f0;border-radius:10px;margin-top:8px;font-size:14px}}
.scope input{{width:20px;height:20px;margin-top:2px;accent-color:#15803d}}
button{{width:100%;margin-top:16px;padding:14px;font-size:16px;border:0;border-radius:10px;background:#15803d;color:#fff}}
.err{{background:#fef2f2;color:#b91c1c;padding:10px;border-radius:10px;font-size:14px;margin-top:12px}}
.note{{background:#f8fafc;border-radius:10px;padding:10px;font-size:12px;color:#475569;margin-top:12px;line-height:1.6}}
</style></head><body><div class="card">
<h1>授权「{client}」访问 QQ 597</h1><div class="muted">回调地址：{host}</div>
<form method="post">{hidden}
<label>用户名</label><input type="text" name="username" autocomplete="username" autocapitalize="none" value="{username}">
<label>密码</label><input type="password" name="password" autocomplete="current-password">
<div class="scope"><input type="checkbox" name="scope_read" checked disabled><div><b>读取</b><br><span class="muted">{read}</span></div></div>
<div class="scope"><input type="checkbox" name="scope_write" {write_checked}><div><b>写入（低风险 / 提出变更）</b><br><span class="muted">{write}</span></div></div>
{error}<button type="submit">登录并授权</button></form>
<div class="note">AI 助手只能做你本人有权限做的事。质量放行、证据核验、库存过账、密码、人事附件、用户与权限<b>永远不会</b>开放给 AI；
它提出的修改要你在系统里确认后才生效。可随时在「我的设置 → AI 助手」撤销授权。</div>
</div></body></html>"""

PASS = ("response_type", "client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "scope", "resource")


def _render(c: OAuthClient, params: dict, error: str = "", username: str = ""):
    hidden = "".join(f'<input type="hidden" name="{k}" value="{html.escape(str(v))}">' for k, v in params.items() if v)
    want_write = "agent.write" in (params.get("scope") or "agent.read agent.write")
    return HTMLResponse(PAGE.format(client=html.escape(c.name), host=html.escape(urlparse(params["redirect_uri"]).netloc),
                                    hidden=hidden, username=html.escape(username), read=SCOPES["agent.read"],
                                    write=SCOPES["agent.write"], write_checked="checked" if want_write else "",
                                    error=f'<div class="err">{html.escape(error)}</div>' if error else ""))


def _check_request(db: Session, params: dict) -> OAuthClient:
    if params.get("response_type") != "code":
        raise HTTPException(400, "unsupported response_type")
    c = db.get(OAuthClient, params.get("client_id") or "")
    if not c:
        raise HTTPException(400, "unknown client_id")
    if not redirect_ok(c, params.get("redirect_uri") or ""):
        raise HTTPException(400, "redirect_uri not registered")
    if c.secret_hash is None and not params.get("code_challenge"):
        raise HTTPException(400, "PKCE code_challenge required")
    if params.get("code_challenge") and params.get("code_challenge_method", "S256") != "S256":
        raise HTTPException(400, "only S256 supported")
    return c


@router.get("/oauth/authorize")
def authorize_page(request: Request, db: Session = Depends(get_db)):
    if not AGENT_GLOBAL:
        raise HTTPException(403, "agent interface disabled")
    params = {k: request.query_params.get(k) for k in PASS}
    c = _check_request(db, params)
    return _render(c, params)


@router.post("/oauth/authorize")
def authorize_submit(request: Request, username: str = Form(""), password: str = Form(""), scope_write: str | None = Form(None),
                     response_type: str = Form(""), client_id: str = Form(""), redirect_uri: str = Form(""),
                     state: str | None = Form(None), code_challenge: str | None = Form(None),
                     code_challenge_method: str | None = Form(None), scope: str | None = Form(None),
                     resource: str | None = Form(None), db: Session = Depends(get_db)):
    params = {"response_type": response_type, "client_id": client_id, "redirect_uri": redirect_uri, "state": state,
              "code_challenge": code_challenge, "code_challenge_method": code_challenge_method, "scope": scope,
              "resource": resource}
    c = _check_request(db, params)
    try:
        login_guard("pw:" + username.strip())
    except HTTPException as e:
        return _render(c, params, e.detail, username)
    u = db.query(User).filter_by(username=username.strip().lower()).first()
    if not u or not u.active or not check_secret(password, u.password_hash):
        login_failed("pw:" + username.strip())
        audit(db, None, "agent.authorize_failed", "user", username)
        db.commit()
        return _render(c, params, "用户名或密码错误", username)
    if u.role not in AGENT_ROLES:
        return _render(c, params, "该角色不能连接 AI 助手", username)
    if not u.agent_enabled:
        return _render(c, params, "你还没有开启 AI 助手访问。请先在系统「我的设置 → AI 助手」中开启，再回来授权。", username)
    granted = "agent.read agent.write" if scope_write else "agent.read"
    code = secrets.token_urlsafe(32)
    db.add(OAuthCode(code_hash=h(code), client_id=c.id, user_id=u.id, redirect_uri=redirect_uri, code_challenge=code_challenge,
                     scope=granted, resource=resource, expires_at=now() + timedelta(minutes=5)))
    audit(db, u, "agent.authorized", "oauth_client", c.id, client=c.name, scope=granted)
    db.commit()
    q = {"code": code, **({"state": state} if state else {})}
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(q)}", status_code=302)


# ---------- token ----------
def _client_auth(db: Session, client_id: str | None, client_secret: str | None, authorization: str) -> OAuthClient:
    if authorization.startswith("Basic "):
        try:
            cid, _, sec = base64.b64decode(authorization[6:]).decode().partition(":")
            client_id, client_secret = cid, sec
        except Exception:
            raise HTTPException(401, "invalid_client")
    c = db.get(OAuthClient, client_id or "")
    if not c:
        raise HTTPException(401, "invalid_client")
    if c.secret_hash and (not client_secret or h(client_secret) != c.secret_hash):
        raise HTTPException(401, "invalid_client")
    return c


def _issue(db: Session, user_id: str, c: OAuthClient, scope: str) -> dict:
    access, refresh = "qqa_" + secrets.token_urlsafe(32), "qqr_" + secrets.token_urlsafe(32)
    t = AgentToken(user_id=user_id, client_id=c.id, client_name=c.name, access_hash=h(access), refresh_hash=h(refresh),
                   scope=scope, expires_at=now() + ACCESS_TTL, refresh_expires_at=now() + REFRESH_TTL)
    db.add(t)
    return {"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
            "refresh_token": refresh, "scope": scope}


def _err(code: str, desc: str = ""):
    return JSONResponse({"error": code, "error_description": desc}, 400, headers={"Cache-Control": "no-store"})


@router.post("/oauth/token")
def token(grant_type: str = Form(...), code: str | None = Form(None), redirect_uri: str | None = Form(None),
          code_verifier: str | None = Form(None), refresh_token: str | None = Form(None),
          client_id: str | None = Form(None), client_secret: str | None = Form(None),
          authorization: str = Header(default=""), db: Session = Depends(get_db)):
    c = _client_auth(db, client_id, client_secret, authorization)
    if grant_type == "authorization_code":
        oc = db.get(OAuthCode, h(code or ""))
        if not oc or oc.used or oc.client_id != c.id or aware(oc.expires_at) < now():
            return _err("invalid_grant", "code invalid or expired")
        if redirect_uri and redirect_uri != oc.redirect_uri:
            return _err("invalid_grant", "redirect_uri mismatch")
        if oc.code_challenge:
            if not code_verifier:
                return _err("invalid_grant", "code_verifier required")
            calc = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
            if calc != oc.code_challenge:
                return _err("invalid_grant", "PKCE verification failed")
        oc.used = True
        u = db.get(User, oc.user_id)
        if not u or not u.active or not u.agent_enabled:
            return _err("invalid_grant", "user not allowed")
        res = _issue(db, u.id, c, oc.scope)
        db.commit()
        return JSONResponse(res, headers={"Cache-Control": "no-store"})
    if grant_type == "refresh_token":
        t = db.query(AgentToken).filter_by(refresh_hash=h(refresh_token or "")).first()
        if not t or t.revoked or t.client_id != c.id or aware(t.refresh_expires_at) < now():
            return _err("invalid_grant", "refresh token invalid")
        u = db.get(User, t.user_id)
        if not u or not u.active or not u.agent_enabled:
            return _err("invalid_grant", "user not allowed")
        t.revoked = True  # rotation
        res = _issue(db, u.id, c, t.scope)
        db.commit()
        return JSONResponse(res, headers={"Cache-Control": "no-store"})
    return _err("unsupported_grant_type")


@router.post("/oauth/revoke")
def revoke(token: str = Form(...), db: Session = Depends(get_db)):
    t = db.query(AgentToken).filter((AgentToken.access_hash == h(token)) | (AgentToken.refresh_hash == h(token))).first()
    if t:
        t.revoked = True
        db.commit()
    return JSONResponse({}, 200)


# ---------- bearer validation for /mcp and /agent/v1 ----------
class AgentCtx:
    def __init__(self, user: User, token: AgentToken, base: str):
        self.user, self.token, self.base = user, token, base
        self.scopes = set(token.scope.split())
        self.client = token.client_name

    def link(self, path: str) -> str:
        return f"{self.base}{path}"


def agent_auth(request: Request, db: Session) -> AgentCtx:
    base = public_base(request)
    challenge = {"WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'}
    if not AGENT_GLOBAL:
        raise HTTPException(403, "AI 助手接口已被管理员关闭")
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "需要授权", headers=challenge)
    t = db.query(AgentToken).filter_by(access_hash=h(auth[7:])).first()
    if not t or t.revoked or aware(t.expires_at) < now():
        raise HTTPException(401, "令牌无效或已过期", headers={**challenge, "WWW-Authenticate": challenge["WWW-Authenticate"] + ', error="invalid_token"'})
    u = db.get(User, t.user_id)
    if not u or not u.active or not u.agent_enabled:
        raise HTTPException(401, "该用户已关闭 AI 助手访问或已停用", headers=challenge)
    if not t.last_used_at or (now() - aware(t.last_used_at)).total_seconds() > 60:
        t.last_used_at = now()
        db.commit()
    return AgentCtx(u, t, base)
