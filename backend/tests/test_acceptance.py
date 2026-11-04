"""Acceptance cases from PRD §12 (IDs in test names)."""
from conftest import PDF, key, png


def master(client, h):
    return client.get("/api/inv/master", headers=h).json()


def stock_row(client, h, sku_code, wh_code):
    m = master(client, h)
    wh = next(w for w in m["warehouses"] if w["code"] == wh_code)
    rows = client.get("/api/inv/stock", headers=h).json()["rows"]
    return next((r for r in rows if r["sku_code"] == sku_code and r["warehouse_id"] == wh["id"]), None)


# ---------- pages / modules ----------
def test_MOD01_create_publish_page_from_template(client, login):
    h = login("ops")
    pid = client.post("/api/pages", json={"name": "冷库巡检", "template": "periodic_check", "visible_roles": ["staff", "ops_manager"]}, headers=h).json()["id"]
    r = client.post(f"/api/pages/{pid}/publish", json={"note": "v1"}, headers=h)
    assert r.status_code == 200, r.text
    staff = login("staff1")
    assert any(p["id"] == pid for p in client.get("/api/pages", headers=staff).json())
    assert client.get(f"/api/pages/{pid}", headers=login("hrfin")).status_code == 403


def test_MOD03_MOD04_reorder_hide_rename_copy_rollback(client, login):
    h = login("ops")
    pid = client.post("/api/pages", json={"name": "测试页", "template": "task_board"}, headers=h).json()["id"]
    pg = client.get(f"/api/pages/{pid}?draft=1", headers=h).json()
    client.post(f"/api/pages/{pid}/publish", json={}, headers=h)
    mods = pg["draft"]["modules"]
    ids = [m["id"] for m in mods]
    # reorder + hide
    mods = list(reversed(mods))
    mods[0]["hidden"] = True
    r = client.put(f"/api/pages/{pid}/draft", json={"layout": {"modules": mods}, "draft_rev": pg["page"]["draft_rev"]}, headers=h)
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["draft"]["modules"]] == list(reversed(ids))  # stable module IDs
    # stale draft rev → conflict
    assert client.put(f"/api/pages/{pid}/draft", json={"layout": {"modules": mods}, "draft_rev": pg["page"]["draft_rev"]}, headers=h).status_code == 409
    client.post(f"/api/pages/{pid}/publish", json={}, headers=h)
    # rename keeps id
    client.patch(f"/api/pages/{pid}", json={"name": "测试页（改名）"}, headers=h)
    assert client.get(f"/api/pages/{pid}", headers=h).json()["page"]["name"] == "测试页（改名）"
    # rollback to v1
    vs = client.get(f"/api/pages/{pid}/versions", headers=h).json()
    v1 = next(v for v in vs if v["version_no"] == 1)
    assert client.post(f"/api/pages/{pid}/rollback", json={"version_id": v1["id"]}, headers=h).status_code == 200
    assert [m["id"] for m in client.get(f"/api/pages/{pid}", headers=h).json()["layout"]["modules"]] == ids
    # copy: new module ids, no data copied
    cid = client.post(f"/api/pages/{pid}/copy", json={"name": "副本"}, headers=h).json()["id"]
    cmods = client.get(f"/api/pages/{cid}?draft=1", headers=h).json()["draft"]["modules"]
    assert not set(m["id"] for m in cmods) & set(ids)


def test_MOD_publish_blocked_with_errors_keeps_draft(client, login):
    h = login("ops")
    pid = client.post("/api/pages", json={"name": "台账测试", "template": "ledger"}, headers=h).json()["id"]
    r = client.post(f"/api/pages/{pid}/publish", json={}, headers=h)
    assert r.status_code == 422  # record type not selected
    assert client.get(f"/api/pages/{pid}?draft=1", headers=h).json()["draft"]["modules"]


def test_MOD_page_admin_has_no_business_data_rights(client, login):
    h = login("pageadmin")
    assert client.post("/api/inv/receive", json={}, headers=h).status_code in (403, 422)
    accs = client.get("/api/nb/accounts", headers=h).json()
    a = accs[0]
    assert "password" not in a and "password_enc" not in a
    assert client.post(f"/api/nb/accounts/{a['id']}/reveal", headers=h).status_code == 403  # NB-02


def test_MOD05_dangerous_field_changes_blocked(client, login):
    h = login("ops")
    rt = client.post("/api/record-types", json={"name": "字段测试", "statuses": ["开", "关"]}, headers=h).json()
    f = client.post(f"/api/record-types/{rt['id']}/fields", json={"label": "数量", "ftype": "number"}, headers=h).json()
    client.post("/api/records", json={"record_type_id": rt["id"], "title": "r1", "data": {f["id"]: 5}}, headers=h)
    assert client.delete(f"/api/fields/{f['id']}", headers=h).status_code == 409
    assert client.patch(f"/api/fields/{f['id']}", json={"label": "数量", "ftype": "text"}, headers=h).status_code == 409
    # rename keeps id
    r = client.patch(f"/api/fields/{f['id']}", json={"label": "箱数", "ftype": "number"}, headers=h)
    assert r.status_code == 200 and r.json()["id"] == f["id"]
    # new required field on existing records requires explicit handling
    assert client.post(f"/api/record-types/{rt['id']}/fields", json={"label": "必填", "required": True}, headers=h).status_code == 409


# ---------- evidence ----------
def test_EVD02_one_file_two_links_separate_verification(client, login):
    buyer, fin = login("buyer"), login("hrfin")
    up = client.post("/api/evidence/upload", files={"file": ("inv.pdf", PDF + b"x1", "application/pdf")},
                     data={"target_type": "record", "target_id": "rec_po", "purpose": "发票", "sensitivity": "finance"}, headers=buyer).json()
    r2 = client.post("/api/evidence/link", json={"evidence_id": up["evidence_id"], "target_type": "record", "target_id": "rec_reimb", "purpose": "报销凭证"}, headers=fin)
    assert r2.status_code == 200
    # same bytes again → dedup, no second original
    again = client.post("/api/evidence/upload", files={"file": ("inv2.pdf", PDF + b"x1", "application/pdf")}, headers=buyer).json()
    assert again["dedup"] and again["evidence_id"] == up["evidence_id"]
    # verify on purchase side only
    assert client.post(f"/api/evidence/links/{up['link_id']}/verify", json={"status": "approved"}, headers=fin).status_code == 200
    reimb = client.get("/api/evidence?target_type=record&target_id=rec_reimb", headers=fin).json()[0]
    assert reimb["verify_status"] == "unverified"
    # SEC: staff cannot read finance evidence
    staff_view = client.get("/api/evidence?target_type=record&target_id=rec_po", headers=login("staff1")).json()[0]
    assert staff_view["readable"] is False and staff_view["url"] is None


def test_EVD_self_verify_blocked(client, login):
    h = login("wh")
    up = client.post("/api/evidence/upload", files={"file": ("a.png", png(1), "image/png")}, data={"target_type": "task", "target_id": "t_x"}, headers=h).json()
    assert client.post(f"/api/evidence/links/{up['link_id']}/verify", json={"status": "approved"}, headers=h).status_code == 409


def test_EVD03_replace_keeps_old_approval(client, login):
    wh, ops = login("wh"), login("ops")
    up = client.post("/api/evidence/upload", files={"file": ("pod.png", png(2), "image/png")}, data={"target_type": "record", "target_id": "rec_q", "purpose": "POD"}, headers=wh).json()
    client.post(f"/api/evidence/links/{up['link_id']}/verify", json={"status": "approved"}, headers=ops)
    client.post(f"/api/evidence/{up['evidence_id']}/versions", files={"file": ("pod_v2.png", png(3), "image/png")}, headers=wh)
    links = client.get("/api/evidence?target_type=record&target_id=rec_q", headers=ops).json()
    assert sorted((l["version_no"], l["verify_status"]) for l in links) == [(1, "approved"), (2, "unverified")]


def test_EVD_reject_bad_type_and_signed_url(client, login):
    h = login("wh")
    assert client.post("/api/evidence/upload", files={"file": ("x.exe", b"MZ\x90\x00", "application/octet-stream")}, headers=h).status_code == 415
    up = client.post("/api/evidence/upload", files={"file": ("b.png", png(4), "image/png")}, data={"target_type": "task", "target_id": "t_y"}, headers=h).json()
    url = client.get("/api/evidence?target_type=task&target_id=t_y", headers=h).json()[0]["url"]
    assert client.get(url).status_code == 200
    assert client.get(url.replace("&s=", "&s=x")).status_code == 403


# ---------- inventory ----------
def test_INV01_partial_ship_receive_conservation(client, login):
    wh = login("wh")
    m = master(client, wh)
    W = {w["code"]: w["id"] for w in m["warehouses"]}
    S = {s["code"]: s["id"] for s in m["skus"]}
    client.post("/api/inv/receive", json={"idempotency_key": key(), "sku_id": S["PK-TRAY"], "warehouse_id": W["EXT1"], "qty": 100, "lot_code": "T1"}, headers=wh)
    lot = next(l for l in master(client, wh)["lots"] if l["lot_code"] == "T1")
    tr = client.post("/api/inv/transfers", json={"from_wh": W["EXT1"], "to_wh": W["WH597"], "lines": [{"sku_id": S["PK-TRAY"], "lot_id": lot["id"], "qty": 60}]}, headers=wh).json()
    assert stock_row(client, wh, "PK-TRAY", "WH597") is None  # request does not move stock
    line = tr["lines"][0]["id"]
    client.post(f"/api/inv/transfers/{tr['id']}/ship", json={"idempotency_key": key(), "lines": [{"line_id": line, "qty": 60}]}, headers=wh)
    r = client.post(f"/api/inv/transfers/{tr['id']}/receive", json={"idempotency_key": key(), "lines": [{"line_id": line, "qty_received": 50, "qty_damaged": 4}]}, headers=wh).json()
    assert r["transfer"]["status"] == "partial"
    src, dst = stock_row(client, wh, "PK-TRAY", "EXT1"), stock_row(client, wh, "PK-TRAY", "WH597")
    assert src["on_hand"] == 40 and dst["on_hand"] == 50 and dst["in_transit"] == 10
    assert src["on_hand"] + dst["on_hand"] + dst["in_transit"] == 100  # conserved
    tasks = client.get(f"/api/tasks?target_id={tr['id']}", headers=wh).json()
    assert tasks and tasks[0]["kind"] == "exception"


def test_INV02_idempotent_and_concurrent_edit(client, login):
    wh = login("wh")
    m = master(client, wh)
    S = {s["code"]: s["id"] for s in m["skus"]}
    W = {w["code"]: w["id"] for w in m["warehouses"]}
    k = key()
    body = {"idempotency_key": k, "sku_id": S["RM-SPICE"], "warehouse_id": W["WH597"], "qty": 5, "lot_code": "S001"}
    before = stock_row(client, wh, "RM-SPICE", "WH597")["on_hand"]
    client.post("/api/inv/receive", json=body, headers=wh)
    second = client.post("/api/inv/receive", json=body, headers=wh).json()
    assert second["duplicate"] is True
    assert stock_row(client, wh, "RM-SPICE", "WH597")["on_hand"] == before + 5
    # record concurrent edit
    ops = login("ops")
    rt = client.get("/api/record-types", headers=ops).json()[0]
    rec = client.post("/api/records", json={"record_type_id": rt["id"], "title": "并发"}, headers=ops).json()
    assert client.patch(f"/api/records/{rec['id']}", json={"version": 1, "title": "A"}, headers=ops).status_code == 200
    assert client.patch(f"/api/records/{rec['id']}", json={"version": 1, "title": "B"}, headers=login("buyer")).status_code == 409


def test_unit_conversion_must_be_defined(client, login):
    wh = login("wh")
    S = {s["code"]: s["id"] for s in master(client, wh)["skus"]}
    W = {w["code"]: w["id"] for w in master(client, wh)["warehouses"]}
    r = client.post("/api/inv/receive", json={"idempotency_key": key(), "sku_id": S["PK-TRAY"], "warehouse_id": W["WH597"], "qty": 1, "unit": "托盘"}, headers=wh)
    assert r.status_code == 400


def test_IMP01_unverified_not_available(client, login):
    wh = login("wh")
    row = stock_row(client, wh, "RM-BEEF", "WH597")
    assert row["unverified"] == 20 and row["available"] == 0 and row["below_safety"]


def test_QLT01_quarantined_lot_cannot_ship(client, login):
    wh, qa = login("wh"), login("qa")
    m = master(client, wh)
    S = {s["code"]: s["id"] for s in m["skus"]}
    W = {w["code"]: w["id"] for w in m["warehouses"]}
    lot = next(l for l in m["lots"] if l["lot_code"] == "S001")
    client.post("/api/inv/reservations", json={"sku_id": S["RM-SPICE"], "warehouse_id": W["WH597"], "lot_id": lot["id"], "qty": 3, "order_ref": "PO-1", "customer": "客户A"}, headers=login("ops"))
    r = client.post(f"/api/inv/lots/{lot['id']}/quarantine", json={"reason": "包装破损"}, headers=qa).json()
    assert r["affected_orders"][0]["order_ref"] == "PO-1"
    resp = client.post("/api/inv/issue", json={"idempotency_key": key(), "sku_id": S["RM-SPICE"], "warehouse_id": W["WH597"], "lot_id": lot["id"], "qty": 1}, headers=wh)
    assert resp.status_code == 409
    assert client.post(f"/api/inv/lots/{lot['id']}/release", json={"reason": "复检", "basis": "x"}, headers=wh).status_code == 403
    assert client.post(f"/api/inv/lots/{lot['id']}/release", json={"reason": "复检合格", "basis": "复检报告"}, headers=qa).status_code == 200


def test_TRC01_production_and_trace(client, login):
    prod, wh, qa = login("prod"), login("wh"), login("qa")
    m = master(client, prod)
    S = {s["code"]: s["id"] for s in m["skus"]}
    W = {w["code"]: w["id"] for w in m["warehouses"]}
    spice = next(l for l in m["lots"] if l["lot_code"] == "S001")
    b = client.post("/api/inv/batches", json={"idempotency_key": key(), "product_sku_id": S["FG-SOUP"], "warehouse_id": W["WH597"], "output_lot_code": "FG-001", "output_qty": 10, "inputs": [{"lot_id": spice["id"], "qty": 2}]}, headers=prod)
    assert b.status_code == 200, b.text
    out_lot = b.json()["output_lot_id"]
    # 生产完工不代表可发货
    assert client.post("/api/inv/issue", json={"idempotency_key": key(), "sku_id": S["FG-SOUP"], "warehouse_id": W["WH597"], "lot_id": out_lot, "qty": 1}, headers=wh).status_code == 409
    client.post(f"/api/inv/lots/{out_lot}/release", json={"reason": "检验合格", "basis": "检验记录"}, headers=qa)
    assert client.post("/api/inv/issue", json={"idempotency_key": key(), "sku_id": S["FG-SOUP"], "warehouse_id": W["WH597"], "lot_id": out_lot, "qty": 4, "order_ref": "SO-9", "customer": "客户B"}, headers=wh).status_code == 200
    fwd = client.get(f"/api/inv/trace/{spice['id']}", headers=qa).json()["forward"]
    assert any(s["order_ref"] == "SO-9" for n in fwd for s in n["shipments"])
    back = client.get(f"/api/inv/trace/{out_lot}", headers=qa).json()["backward"]
    assert back[0]["lot_code"] == "S001"


def test_reverse_posting(client, login):
    wh = login("wh")
    S = {s["code"]: s["id"] for s in master(client, wh)["skus"]}
    W = {w["code"]: w["id"] for w in master(client, wh)["warehouses"]}
    before = stock_row(client, wh, "PK-TRAY", "WH597")["on_hand"]
    p = client.post("/api/inv/receive", json={"idempotency_key": key(), "sku_id": S["PK-TRAY"], "warehouse_id": W["WH597"], "qty": 7, "lot_code": "T1"}, headers=wh).json()
    assert client.post(f"/api/inv/postings/{p['posting_id']}/reverse", json={"idempotency_key": key(), "reason": "录错"}, headers=wh).status_code == 200
    assert stock_row(client, wh, "PK-TRAY", "WH597")["on_hand"] == before
    assert client.post(f"/api/inv/postings/{p['posting_id']}/reverse", json={"idempotency_key": key(), "reason": "again"}, headers=wh).status_code == 409


# ---------- checklists / tasks ----------
def _quality_page(client, h):
    pages = client.get("/api/pages", headers=h).json()
    p = next(p for p in pages if p["name"] == "质量与食品安全")
    layout = client.get(f"/api/pages/{p['id']}", headers=h).json()["layout"]
    ck = next(m for m in layout["modules"] if m["type"] == "checklist")
    return p["id"], ck


def test_DASH01_missing_checklist_then_OPS01_failure_flow(client, login):
    staff, ops, qa = login("staff1"), login("ops"), login("qa")
    m0 = client.get("/api/metrics?keys=missing_checklists", headers=ops).json()[0]
    assert m0["value"] >= 1 and m0["status"] == "warn"
    pid, ck = _quality_page(client, staff)
    photo = client.post("/api/evidence/upload", files={"file": ("s.png", png(9), "image/png")}, headers=staff).json()
    res = [{"key": "hands", "result": "pass"}, {"key": "surface", "result": "fail", "evidence_version_ids": [photo["version_id"]]},
           {"key": "cooler", "result": "pass", "value": 38}]
    r = client.post(f"/api/checklists/{pid}/{ck['id']}/runs", json={"results": res}, headers=staff).json()
    assert r["passed"] is False and r["task_id"]
    tid = r["task_id"]
    t = client.get(f"/api/tasks/{tid}", headers=staff).json()["task"]
    # staff reports done → review only; cannot mark done
    assert client.patch(f"/api/tasks/{tid}", json={"version": t["version"], "status": "done"}, headers=staff).status_code == 403
    # upload fix photo but not verified → verifier cannot close
    client.post("/api/evidence/upload", files={"file": ("fix.png", png(10), "image/png")}, data={"target_type": "task", "target_id": tid, "purpose": "整改证明"}, headers=staff)
    t = client.get(f"/api/tasks/{tid}", headers=staff).json()["task"]
    assert client.patch(f"/api/tasks/{tid}", json={"version": t["version"], "status": "done"}, headers=qa).status_code == 409
    link = client.get(f"/api/evidence?target_type=task&target_id={tid}", headers=qa).json()[0]
    client.post(f"/api/evidence/links/{link['link_id']}/verify", json={"status": "approved"}, headers=qa)
    assert client.patch(f"/api/tasks/{tid}", json={"version": t["version"], "status": "done"}, headers=qa).status_code == 200


def test_checklist_requires_evidence_and_na_reason(client, login):
    staff = login("staff2")
    pid, ck = _quality_page(client, staff)
    res = [{"key": "hands", "result": "na"}, {"key": "surface", "result": "pass"}, {"key": "cooler", "result": "pass", "value": 38}]
    assert client.post(f"/api/checklists/{pid}/{ck['id']}/runs", json={"results": res}, headers=staff).status_code == 400


def test_record_status_requires_evidence(client, login):
    ops, wh = login("ops"), login("wh")
    rts = client.get("/api/record-types", headers=ops).json()
    eq = next(r for r in rts if r["name"] == "设备与建设")
    rec = client.post("/api/records", json={"record_type_id": eq["id"], "title": "测试设备"}, headers=ops).json()
    r = client.patch(f"/api/records/{rec['id']}", json={"version": rec["version"], "status": "已安装"}, headers=ops)
    assert r.status_code == 409 and "安装后照片" in r.text
    client.post("/api/evidence/upload", files={"file": ("after.png", png(11), "image/png")}, data={"target_type": "record", "target_id": rec["id"], "purpose": "安装后照片"}, headers=wh)
    assert client.patch(f"/api/records/{rec['id']}", json={"version": rec["version"], "status": "已安装"}, headers=ops).status_code == 200


# ---------- ingest ----------
def test_ING01_ING04_changeset_apply_and_conflict(client, login):
    ops, buyer = login("ops"), login("buyer")
    tasks = client.get("/api/tasks", headers=ops).json()
    t = next(x for x in tasks if x["title"].startswith("Penske"))
    it = client.post("/api/ingest", files={"file": ("chat.png", png(12), "image/png")}, data={"mode": "suggest"}, headers=ops).json()
    cs = client.post(f"/api/ingest/{it['id']}/changesets", json={"target_type": "task", "target_id": t["id"], "changes": [
        {"field": "due_at", "new": "2026-11-12T17:00:00+00:00", "reason": "截图交期周三"},
        {"field": "next_step", "new": "确认 TT 配送影响"}]}, headers=ops).json()
    assert cs["changes"][0]["old"] is None
    # unregistered field rejected
    assert client.post(f"/api/ingest/{it['id']}/changesets", json={"target_type": "task", "target_id": t["id"], "changes": [{"field": "owner_password", "new": "x"}]}, headers=ops).status_code == 400
    r = client.post(f"/api/changesets/{cs['id']}/apply", json={"accept": ["due_at"]}, headers=buyer)
    assert r.status_code == 200 and r.json()["status"] == "partial"
    links = client.get(f"/api/evidence?target_type=task&target_id={t['id']}", headers=ops).json()
    assert any(l["purpose"] == "变更依据" for l in links)
    # conflict: propose, then someone edits, then apply
    cs2 = client.post(f"/api/ingest/{it['id']}/changesets", json={"target_type": "task", "target_id": t["id"], "changes": [{"field": "next_step", "new": "X"}]}, headers=ops).json()
    cur = client.get(f"/api/tasks/{t['id']}", headers=ops).json()["task"]
    client.patch(f"/api/tasks/{t['id']}", json={"version": cur["version"], "next_step": "别人改了"}, headers=ops)
    assert client.post(f"/api/changesets/{cs2['id']}/apply", json={"accept": ["next_step"]}, headers=ops).status_code == 409
    assert client.get(f"/api/tasks/{t['id']}", headers=ops).json()["task"]["next_step"] == "别人改了"
    assert client.get(f"/api/ingest/{it['id']}", headers=ops).json()["status"] == "conflict"


# ---------- notebook ----------
def test_NB_shared_password_audit_and_offboarding(client, login):
    ops, buyer, staff, admin = login("ops"), login("buyer"), login("staff1"), login("admin")
    a = client.post("/api/nb/accounts", json={"service": "测试门户", "password": "S3cret!", "share_roles": ["purchaser"]}, headers=ops).json()
    assert "S3cret" not in str(a)
    assert client.post(f"/api/nb/accounts/{a['id']}/reveal", headers=buyer).json()["password"] == "S3cret!"
    assert client.post(f"/api/nb/accounts/{a['id']}/reveal", headers=staff).status_code == 403
    audit = client.get(f"/api/audit?target_id={a['id']}", headers=ops).json()
    assert any(x["action"] == "account.password_viewed" for x in audit)
    assert "S3cret" not in str(audit)
    assert all("S3cret" not in str(x) for x in client.get("/api/search?q=测试", headers=ops).json())
    # offboarding buyer → rotation task
    users = client.get("/api/users", headers=admin).json()
    b = next(x for x in users if x.get("username") == "buyer")
    client.patch(f"/api/users/{b['id']}", json={"username": "buyer", "display_name": b["name"], "role": "purchaser", "active": False}, headers=admin)
    assert any(t["title"].startswith("轮换共享密码：测试门户") for t in client.get("/api/tasks", headers=ops).json())
    assert client.get("/api/me", headers=buyer).status_code == 401  # revocation immediate


def test_SEC_login_rate_limit(client):
    for _ in range(8):
        assert client.post("/api/auth/login", json={"username": "nobody_x", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody_x", "password": "wrong"}).status_code == 429
