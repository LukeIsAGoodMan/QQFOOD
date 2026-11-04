"""Agent interface (PRD §9.11, T-AGT-*)."""
import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlparse

from conftest import png


def pkce():
    v = secrets.token_urlsafe(48)
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
    return v, c


def enable(client, login, user, on=True):
    assert client.post("/api/agent/enable", json={"enabled": on}, headers=login(user)).status_code == 200


def connect(client, user, scope_write=True, redirect="https://chatgpt.com/connector_platform_oauth_redirect"):
    reg = client.post("/oauth/register", json={"client_name": "ChatGPT", "redirect_uris": [redirect]}).json()
    v, c = pkce()
    q = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": redirect, "state": "xyz",
         "code_challenge": c, "code_challenge_method": "S256", "scope": "agent.read agent.write", "resource": "http://testserver/mcp"}
    assert client.get("/oauth/authorize", params=q).status_code == 200
    form = {**q, "username": user, "password": "qq597demo", **({"scope_write": "on"} if scope_write else {})}
    r = client.post("/oauth/authorize", data=form, follow_redirects=False)
    assert r.status_code == 302, r.text[:300]
    loc = urlparse(r.headers["location"])
    code = parse_qs(loc.query)["code"][0]
    assert parse_qs(loc.query)["state"] == ["xyz"]
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                            "code_verifier": v, "client_id": reg["client_id"]}).json()
    assert "access_token" in tok, tok
    return reg["client_id"], tok


def rpc(client, tok, method, params=None, id_=1):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
                    headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert r.status_code == 200, r.text
    return r.json()


def call(client, tok, name, args=None):
    res = rpc(client, tok, "tools/call", {"name": name, "arguments": args or {}})["result"]
    return res


def test_AGT01_discovery_and_401_challenge(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]
    md = client.get("/.well-known/oauth-protected-resource").json()
    assert md["resource"].endswith("/mcp")
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert "S256" in asm["code_challenge_methods_supported"] and asm["registration_endpoint"]


def test_AGT02_must_enable_first_and_sysadmin_blocked(client, login):
    reg = client.post("/oauth/register", json={"client_name": "x", "redirect_uris": ["https://chatgpt.com/cb"]}).json()
    v, c = pkce()
    form = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": "https://chatgpt.com/cb", "code_challenge": c,
            "code_challenge_method": "S256", "username": "qa", "password": "qq597demo"}
    r = client.post("/oauth/authorize", data=form, follow_redirects=False)
    assert r.status_code == 200 and "还没有开启" in r.text
    enable(client, login, "admin")
    r = client.post("/oauth/authorize", data={**form, "username": "admin"}, follow_redirects=False)
    assert "不能连接" in r.text
    # unregistered redirect rejected
    assert client.get("/oauth/authorize", params={**form, "redirect_uri": "https://evil.example/cb"}).status_code == 400


def test_AGT03_mcp_read_tools_and_overview(client, login):
    enable(client, login, "boss")
    _, tok = connect(client, "boss", scope_write=False)
    init = rpc(client, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert init["result"]["protocolVersion"] == "2025-06-18" and "instructions" in init["result"]
    names = {t["name"] for t in rpc(client, tok, "tools/list")["result"]["tools"]}
    assert "get_overview" in names and "propose_change" not in names  # read-only grant hides write tools
    assert not names & {"release_lot", "verify_evidence", "reveal_password", "post_inventory"}
    res = call(client, tok, "get_overview")
    assert not res["isError"] and any(m["key"] == "below_safety" for m in res["structuredContent"]["metrics"])
    # write tool with read-only token → refused
    res = call(client, tok, "add_comment", {"target_type": "task", "target_id": "x", "text": "hi"})
    assert res["isError"] and "写入" in res["content"][0]["text"]


def test_AGT04_propose_change_needs_human_confirmation(client, login):
    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    tasks = call(client, tok, "search", {"query": "Penske"})["structuredContent"]["results"]
    tid = next(r["id"] for r in tasks if r["type"] == "task")
    before = call(client, tok, "get_task", {"task_id": tid})["structuredContent"]
    res = call(client, tok, "propose_change", {"target_type": "task", "target_id": tid, "source_summary": "Wayne 微信：车周三到",
                                                "changes": [{"field": "下一步", "new_value": "确认周三提车"}, {"field": "截止时间", "new_value": "2026-11-12"}]})
    out = res["structuredContent"]
    assert out["status"] == "pending_confirmation" and "/ingest?item=" in out["confirm_url"]
    after = call(client, tok, "get_task", {"task_id": tid})["structuredContent"]
    assert after["next_step"] == before["next_step"]  # not applied yet
    # unregistered field refused
    bad = call(client, tok, "propose_change", {"target_type": "task", "target_id": tid, "source_summary": "x", "changes": [{"field": "owner_password", "new_value": "1"}]})
    assert bad["isError"]
    # human confirms in the app
    iid = out["confirm_url"].split("item=")[1]
    item = client.get(f"/api/ingest/{iid}", headers=login("ops")).json()
    assert item["source"] == "agent" and item["evidence"]["title"].startswith("AI 转述")
    cs = item["changesets"][0]
    r = client.post(f"/api/changesets/{cs['id']}/apply", json={"accept": [c["field"] for c in cs["changes"]]}, headers=login("ops"))
    assert r.status_code == 200
    final = call(client, tok, "get_task", {"task_id": tid})["structuredContent"]
    assert final["next_step"] == "确认周三提车" and final["due_at"].startswith("2026-11-13T01:00")  # 17:00 LA = 01:00Z


def test_AGT05_low_risk_writes_and_limits(client, login):
    enable(client, login, "wh")
    _, tok = connect(client, "wh")
    t = call(client, tok, "create_task", {"title": "AI 上报：冷库门把手松动", "kind": "exception"})["structuredContent"]["task"]
    assert t["requires_evidence"] and t["status"] == "todo"
    r = call(client, tok, "update_task_progress", {"task_id": t["id"], "version": t["version"], "status": "done"})
    assert r["isError"]  # cannot mark done
    r = call(client, tok, "update_task_progress", {"task_id": t["id"], "version": t["version"], "status": "review", "next_step": "等维修"})
    assert not r["isError"]
    r = call(client, tok, "update_task_progress", {"task_id": t["id"], "version": t["version"], "next_step": "旧版本"})
    assert r["isError"] and "修改" in r["content"][0]["text"]  # version conflict
    hist = client.get(f"/api/tasks/{t['id']}", headers=login("ops")).json()["history"]
    assert hist[0]["via"].startswith("agent:")


def test_AGT06_no_secrets_no_hr_evidence(client, login):
    enable(client, login, "staff2")
    _, tok = connect(client, "staff2")
    client.post("/api/evidence/upload", files={"file": ("hr.png", png(77), "image/png")},
                data={"target_type": "task", "target_id": "t_hr", "sensitivity": "hr", "title": "员工证件"}, headers=login("hrfin"))
    for name, args in [("search", {"query": "示例"}), ("search", {"query": "员工证件"}), ("list_pending_evidence", {}), ("find_contacts", {"query": "W"})]:
        text = call(client, tok, name, args)["content"][0]["text"]
        assert "demo-only-123" not in text and "员工证件" not in text and "password" not in text.lower()


def test_AGT07_kill_switch_and_refresh_rotation(client, login):
    enable(client, login, "prod")
    cid, tok = connect(client, "prod")
    new = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid}).json()
    assert "access_token" in new
    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid}).status_code == 400
    st = client.get("/api/agent/status", headers=login("prod")).json()
    assert st["connections"] and st["connections"][0]["client"] == "ChatGPT"
    enable(client, login, "prod", on=False)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": f"Bearer {new['access_token']}"})
    assert r.status_code == 401


def test_AGT08_upload_link_original_file(client, login):
    enable(client, login, "qa")
    _, tok = connect(client, "qa")
    tid = call(client, tok, "list_tasks", {})["structuredContent"]["tasks"][0]["id"]
    url = call(client, tok, "request_upload_link", {"target_type": "task", "target_id": tid, "purpose": "整改证明"})["structuredContent"]["upload_url"]
    token = url.split("/u/")[1]
    info = client.get(f"/api/upload-link/{token}").json()
    assert info["purpose"] == "整改证明"
    r = client.post(f"/api/upload-link/{token}", files={"file": ("fix.png", png(88), "image/png")})
    assert r.status_code == 200
    links = client.get(f"/api/evidence?target_type=task&target_id={tid}", headers=login("ops")).json()
    assert any(l["purpose"] == "整改证明" and l["verify_status"] == "unverified" for l in links)
    assert client.get(f"/api/upload-link/{token[:-3]}abc").status_code == 403


def test_AGT09_gpt_actions_confidential_client(client, login):
    from app.agent.oauth import create_admin_client
    from app.db import SessionLocal
    db = SessionLocal()
    cid, secret = create_admin_client(db, "老板的 GPT", ["https://chatgpt.com/aip/"])
    db.close()
    enable(client, login, "boss")
    redirect = "https://chatgpt.com/aip/g-abc123/oauth/callback"
    r = client.post("/oauth/authorize", data={"response_type": "code", "client_id": cid, "redirect_uri": redirect, "state": "s",
                                              "username": "boss", "password": "qq597demo", "scope_write": "on"}, follow_redirects=False)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect},
                      headers={"Authorization": f"Basic {basic}"}).json()
    assert client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code}, headers={"Authorization": f"Basic {basic}"}).status_code == 400  # code single-use
    spec = client.get("/agent/openapi.json").json()
    ops = {p["post"]["operationId"]: p["post"] for p in spec["paths"].values()}
    assert ops["get_overview"]["x-openai-isConsequential"] is False and ops["propose_change"]["x-openai-isConsequential"] is True
    r = client.post("/agent/v1/get_overview", json={}, headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert r.status_code == 200 and r.json()["metrics"]
    # every call audited
    audit = client.get("/api/audit?limit=50", headers=login("boss")).json()
    assert any(a["action"] == "agent.call" and a["detail"].get("tool") == "get_overview" for a in audit)
