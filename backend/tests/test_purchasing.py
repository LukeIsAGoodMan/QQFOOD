"""采购到收货闭环（PRD PUR-01..03，T-PUR-*）。"""
from conftest import PDF, png


def master(client, h):
    return client.get("/api/inv/master", headers=h).json()


def on_hand(client, h, sku_code, wh_code="WH597", bucket="on_hand"):
    m = master(client, h)
    wh = next(w for w in m["warehouses"] if w["code"] == wh_code)
    row = next((r for r in client.get("/api/inv/stock", headers=h).json()["rows"] if r["sku_code"] == sku_code and r["warehouse_id"] == wh["id"]), None)
    return row[bucket] if row else 0


def upload(client, h, target_type, target_id, purpose, n):
    r = client.post("/api/evidence/upload", files={"file": (f"{purpose}.png", png(n), "image/png")},
                    data={"target_type": target_type, "target_id": target_id, "purpose": purpose}, headers=h)
    assert r.status_code == 200, r.text


def test_PUR01_02_requisition_versions_and_quote_compare(client, login):
    buyer = login("buyer")
    reqs = client.get("/api/purchasing/requisitions", headers=buyer).json()
    demo = next(r for r in reqs if r["code"] == "RQ-DEMO-001")
    d = client.get(f"/api/purchasing/requisitions/{demo['id']}", headers=buyer).json()
    assert [v["margin_pct"] for v in d["versions"]] == [15, 5]  # both versions kept, newest first
    assert d["versions"][0]["lines"][0]["required_qty"] == 115  # 100 × 1.15
    stale = [q for q in d["quotes"] if q["stale"]]
    assert len(stale) == 1 and stale[0]["version_no"] == 1
    best = next(q for q in d["quotes"] if q.get("best_price"))
    fast = next(q for q in d["quotes"] if q.get("fastest"))
    assert best["supplier"] == "示例供应商 A" and fast["supplier"] == "示例供应商 B"
    # landed total includes freight/tax/install: A = 115*11.5 + 46*38 + 60
    assert best["total"] == round(115 * 11.5 + 46 * 38 + 60, 2)
    # stale quote cannot become a PO
    assert client.post(f"/api/purchasing/quotes/{stale[0]['id']}/to-po", json={}, headers=buyer).status_code == 409
    r = client.post(f"/api/purchasing/quotes/{best['id']}/to-po", json={"expected_date": "2026-11-20"}, headers=buyer)
    assert r.status_code == 200
    po = client.get(f"/api/purchasing/pos/{r.json()['id']}", headers=buyer).json()
    assert po["lines"][0]["qty_ordered"] == 115 and po["lines"][0]["unit_price"] == 11.5 and po["freight"] == 60
    assert client.get(f"/api/purchasing/requisitions/{demo['id']}", headers=buyer).json()["status"] == "ordered"


def _new_po(client, h, qty=10, price=5.0, sku_code="PK-TRAY", extra_line=False):
    m = master(client, h)
    S = {s["code"]: s["id"] for s in m["skus"]}
    lines = [{"sku_id": S[sku_code], "qty_ordered": qty, "unit_price": price}]
    if extra_line:
        lines.append({"item_name": "研磨机安装服务", "unit": "次", "qty_ordered": 1, "unit_price": 300})
    r = client.post("/api/purchasing/pos", json={"supplier": "测试供应商", "lines": lines, "freight": 20, "expected_date": "2026-01-01"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_PUR_approval_over_limit_requires_boss_signature(client, login):
    buyer, boss, ops = login("buyer"), login("boss"), login("ops")
    small = _new_po(client, buyer, qty=10, price=5)
    assert client.post(f"/api/purchasing/pos/{small}/submit", headers=buyer).json()["status"] == "approved"
    big = _new_po(client, buyer, qty=1000, price=5)  # $5,020 > $2,000
    r = client.post(f"/api/purchasing/pos/{big}/submit", headers=buyer).json()
    assert r["status"] == "pending_approval"
    m = client.get("/api/metrics?keys=po_pending_approval", headers=boss).json()[0]
    assert m["value"] >= 1
    assert client.post(f"/api/purchasing/pos/{big}/approve", json={"decision": "approve"}, headers=ops).status_code == 403
    assert client.post(f"/api/purchasing/pos/{big}/order", headers=buyer).status_code == 409  # not approved yet
    assert client.post(f"/api/purchasing/pos/{big}/approve", json={"decision": "reject"}, headers=boss).status_code == 400  # reason needed
    r = client.post(f"/api/purchasing/pos/{big}/approve", json={"decision": "approve"}, headers=boss).json()
    assert r["status"] == "approved" and r["approval"]["by_name"] == "老板" and r["approval"]["meaning"]
    # money cannot change after submission
    assert client.patch(f"/api/purchasing/pos/{big}", json={"version": r["version"], "freight": 1}, headers=buyer).status_code == 409


def test_PUR03_receive_by_line_needs_signoff_and_is_idempotent(client, login):
    buyer, wh, staff, ops = login("buyer"), login("wh"), login("staff1"), login("ops")
    pid = _new_po(client, buyer, qty=10, price=5, extra_line=True)
    client.post(f"/api/purchasing/pos/{pid}/submit", headers=buyer)
    # receiving before ordering is refused
    po = client.get(f"/api/purchasing/pos/{pid}", headers=buyer).json()
    inv_line, svc_line = po["lines"][0]["id"], po["lines"][1]["id"]
    assert client.post(f"/api/purchasing/pos/{pid}/receipts", json={"lines": [{"po_line_id": inv_line, "qty": 1, "lot_code": "X"}]}, headers=staff).status_code == 409
    client.post(f"/api/purchasing/pos/{pid}/order", headers=buyer)
    before = on_hand(client, wh, "PK-TRAY")
    # reject reason required（食品类必须填 Lot 见 test_categories；托盘是生产用品，Lot 可不填）
    assert client.post(f"/api/purchasing/pos/{pid}/receipts", json={"lines": [{"po_line_id": inv_line, "qty": 6, "lot_code": "P1", "rejected": 1}]}, headers=staff).status_code == 400
    # over-receipt blocked without reason
    assert client.post(f"/api/purchasing/pos/{pid}/receipts", json={"lines": [{"po_line_id": inv_line, "qty": 11, "lot_code": "P1"}]}, headers=staff).status_code == 409
    rc = client.post(f"/api/purchasing/pos/{pid}/receipts", json={"lines": [
        {"po_line_id": inv_line, "qty": 6, "lot_code": "P1", "rejected": 1, "reject_reason": "外箱破损"},
        {"po_line_id": svc_line, "qty": 1}]}, headers=staff).json()
    assert on_hand(client, wh, "PK-TRAY") == before  # submission does not touch stock
    assert client.get("/api/metrics?keys=receipts_pending", headers=ops).json()[0]["value"] >= 1
    # staff cannot post; post needs 签收单
    assert client.post(f"/api/purchasing/receipts/{rc['id']}/post", headers=staff).status_code == 403
    assert client.post(f"/api/purchasing/receipts/{rc['id']}/post", headers=wh).status_code == 409
    upload(client, staff, "receipt", rc["id"], "签收单", 301)
    r = client.post(f"/api/purchasing/receipts/{rc['id']}/post", headers=wh).json()
    assert r["duplicate"] is False and r["po"]["status"] == "partial"
    assert on_hand(client, wh, "PK-TRAY") == before + 6
    again = client.post(f"/api/purchasing/receipts/{rc['id']}/post", headers=wh).json()
    assert again["duplicate"] is True and on_hand(client, wh, "PK-TRAY") == before + 6
    line = r["po"]["lines"][0]
    assert line["qty_received"] == 6 and line["qty_rejected"] == 1 and line["open_qty"] == 4
    assert r["po"]["lines"][1]["qty_received"] == 1  # non-inventory line received without stock posting
    tasks = client.get(f"/api/tasks?target_id={pid}", headers=buyer).json()
    assert tasks and "外箱破损" in tasks[0]["description"]
    # trace: the received lot exists and its stock came from this receipt
    posts = client.get("/api/inv/postings?limit=5", headers=wh).json()
    assert any(p["source_type"] == "receipt" and p["source_id"] == rc["id"] for p in posts)
    # close short
    assert client.post(f"/api/purchasing/pos/{pid}/close", json={"reason": ""}, headers=buyer).status_code == 400
    closed = client.post(f"/api/purchasing/pos/{pid}/close", json={"reason": "供应商缺货，剩余 4 箱不再发"}, headers=buyer).json()
    assert closed["status"] == "closed"


def test_PUR_unverified_receipt_goes_to_unverified_bucket(client, login):
    buyer, wh = login("buyer"), login("wh")
    pid = _new_po(client, buyer, qty=3, price=5, sku_code="RM-SPICE")
    client.post(f"/api/purchasing/pos/{pid}/submit", headers=buyer)
    client.post(f"/api/purchasing/pos/{pid}/order", headers=buyer)
    line = client.get(f"/api/purchasing/pos/{pid}", headers=buyer).json()["lines"][0]["id"]
    before = on_hand(client, wh, "RM-SPICE", bucket="unverified")
    rc = client.post(f"/api/purchasing/pos/{pid}/receipts", json={"lines": [{"po_line_id": line, "qty": 3, "lot_code": "S9", "counted": False}]}, headers=wh).json()
    upload(client, wh, "receipt", rc["id"], "签收单", 302)
    r = client.post(f"/api/purchasing/receipts/{rc['id']}/post", headers=wh).json()
    assert r["po"]["status"] == "received" and r["po"]["receipts"][0]["self_posted"] is True
    assert on_hand(client, wh, "RM-SPICE", bucket="unverified") == before + 3


def test_PUR_invoice_match_and_payment_reconcile(client, login):
    buyer, wh, fin = login("buyer"), login("wh"), login("hrfin")
    pid = _new_po(client, buyer, qty=4, price=10)  # goods 40 + freight 20
    client.post(f"/api/purchasing/pos/{pid}/submit", headers=buyer)
    client.post(f"/api/purchasing/pos/{pid}/order", headers=buyer)
    line = client.get(f"/api/purchasing/pos/{pid}", headers=buyer).json()["lines"][0]["id"]
    rc = client.post(f"/api/purchasing/pos/{pid}/receipts", json={"lines": [{"po_line_id": line, "qty": 4, "lot_code": "T4"}]}, headers=wh).json()
    upload(client, wh, "receipt", rc["id"], "签收单", 303)
    client.post(f"/api/purchasing/receipts/{rc['id']}/post", headers=wh)
    po = client.post(f"/api/purchasing/pos/{pid}/invoice", json={"invoice_no": "INV-77", "invoice_amount": 75}, headers=buyer).json()
    assert po["payment_status"] == "invoiced" and po["match"]["payable"] == 60 and po["match"]["ok"] is False and po["match"]["diff"] == 15
    # payment proof required; upload ≠ reconciled
    assert client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "paid_pending"}, headers=buyer).status_code == 409
    client.post("/api/evidence/upload", files={"file": ("pay.pdf", PDF + b"pay77", "application/pdf")},
                data={"target_type": "po", "target_id": pid, "purpose": "付款证明", "sensitivity": "finance"}, headers=buyer)
    assert client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "paid_pending"}, headers=buyer).json()["payment_status"] == "paid_pending"
    assert client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "reconciled"}, headers=buyer).status_code == 403
    # invoice file required, then mismatch needs explanation
    assert client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "reconciled"}, headers=fin).status_code == 409
    client.post("/api/evidence/upload", files={"file": ("inv.pdf", PDF + b"inv77", "application/pdf")},
                data={"target_type": "po", "target_id": pid, "purpose": "发票", "sensitivity": "finance"}, headers=buyer)
    r = client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "reconciled"}, headers=fin)
    assert r.status_code == 409 and "15" in r.text
    ok = client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "reconciled", "note": "含 15 美元托盘押金"}, headers=fin).json()
    assert ok["payment_status"] == "reconciled" and ok["reconcile"]["by_name"] == "人事财务 Fiona"


def test_PUR_overdue_metric_scan_and_agent(client, login):
    from test_agent import call, connect, enable
    buyer, ops = login("buyer"), login("ops")
    pid = _new_po(client, buyer, qty=2, price=5)
    client.post(f"/api/purchasing/pos/{pid}/submit", headers=buyer)
    po = client.post(f"/api/purchasing/pos/{pid}/order", headers=buyer).json()
    assert po["overdue"] is True  # expected 2026-01-01
    assert client.get("/api/metrics?keys=po_overdue", headers=ops).json()[0]["value"] >= 1
    assert client.get(f"/api/inv/scan/{po['code']}", headers=ops).json() == {"type": "po", "po_id": pid, "code": po["code"]}
    # ChatGPT: read + propose new ETA (not applied until confirmed)
    enable(client, login, "buyer")
    _, tok = connect(client, "buyer")
    d = call(client, tok, "get_purchase_order", {"po": po["code"]})["structuredContent"]
    assert d["code"] == po["code"] and "can" not in d
    res = call(client, tok, "propose_change", {"target_type": "po", "target_id": po["code"], "source_summary": "供应商邮件：改为 11 月 20 日到",
                                                "changes": [{"field": "预计到货", "new_value": "2026-11-20"}]})["structuredContent"]
    assert res["status"] == "pending_confirmation"
    assert client.get(f"/api/purchasing/pos/{pid}", headers=buyer).json()["expected_date"] == "2026-01-01"
    iid = res["confirm_url"].split("item=")[1]
    cs = client.get(f"/api/ingest/{iid}", headers=buyer).json()["changesets"][0]
    assert client.post(f"/api/changesets/{cs['id']}/apply", json={"accept": ["expected_date"]}, headers=buyer).status_code == 200
    after = client.get(f"/api/purchasing/pos/{pid}", headers=buyer).json()
    assert after["expected_date"] == "2026-11-20" and after["overdue"] is False


def test_PUR_settings_only_boss(client, login):
    assert client.put("/api/purchasing/settings", json={"approval_limit": 5000}, headers=login("buyer")).status_code == 403
    assert client.put("/api/purchasing/settings", json={"approval_limit": 2000}, headers=login("boss")).status_code == 200


def test_upgrade_migration_republishes_pages(client, login):
    """Simulate a pre-procurement DB: remove marker, run upgrade, check new page version with old modules hidden."""
    from app.db import SessionLocal
    from app.models import AppSetting
    from app.seed import upgrade_pages
    db = SessionLocal()
    db.delete(db.get(AppSetting, "migration:procurement_v1"))
    db.commit()
    db.close()
    h = login("ops")
    pg = next(p for p in client.get("/api/pages", headers=h).json() if p["name"] == "采购")
    before = client.get(f"/api/pages/{pg['id']}/versions", headers=h).json()[0]["version_no"]
    upgrade_pages()
    upgrade_pages()  # idempotent
    vs = client.get(f"/api/pages/{pg['id']}/versions", headers=h).json()
    assert vs[0]["version_no"] == before + 1
    layout = client.get(f"/api/pages/{pg['id']}", headers=h).json()["layout"]["modules"]
    assert layout[3]["type"] == "purchasing" and all(m["hidden"] for m in layout[5:])


# ---------- flexibility: receipts instead of invoices, exceptions, quick purchases ----------
def test_PUR_receipt_doc_and_exception_paths(client, login):
    buyer, wh, fin = login("buyer"), login("wh"), login("hrfin")
    pid = _new_po(client, buyer, qty=2, price=10, sku_code="RM-SPICE")
    client.post(f"/api/purchasing/pos/{pid}/submit", headers=buyer)
    client.post(f"/api/purchasing/pos/{pid}/order", headers=buyer)
    line = client.get(f"/api/purchasing/pos/{pid}", headers=buyer).json()["lines"][0]["id"]
    rc = client.post(f"/api/purchasing/pos/{pid}/receipts", json={"lines": [{"po_line_id": line, "qty": 2, "lot_code": "S-EX"}]}, headers=wh).json()
    # no sign-off evidence: refused, then allowed with an exception reason
    assert client.post(f"/api/purchasing/receipts/{rc['id']}/post", headers=wh).status_code == 409
    r = client.post(f"/api/purchasing/receipts/{rc['id']}/post", json={"exception_reason": "司机未留签收单，已电话确认"}, headers=wh).json()
    assert r["po"]["status"] == "received" and r["po"]["exceptions"][0]["step"].endswith("无签收依据")
    # store receipt instead of invoice; receipt also counts as payment proof
    po = client.post(f"/api/purchasing/pos/{pid}/invoice", json={"doc_type": "receipt", "invoice_amount": 40}, headers=buyer).json()
    assert po["doc_type"] == "receipt" and po["missing_doc"] is True
    upload(client, buyer, "po", pid, "收据", 401)
    assert client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "paid_pending"}, headers=buyer).status_code == 200
    ok = client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "reconciled"}, headers=fin).json()
    assert ok["payment_status"] == "reconciled"


def test_PUR_no_document_and_cash_payment(client, login):
    buyer, fin = login("buyer"), login("hrfin")
    pid = _new_po(client, buyer, qty=1, price=8)
    client.post(f"/api/purchasing/pos/{pid}/submit", headers=buyer)
    client.post(f"/api/purchasing/pos/{pid}/order", headers=buyer)
    assert client.post(f"/api/purchasing/pos/{pid}/invoice", json={"doc_type": "none", "invoice_amount": 28}, headers=buyer).status_code == 400
    po = client.post(f"/api/purchasing/pos/{pid}/invoice", json={"doc_type": "none", "invoice_amount": 28, "reason": "路边摊现金购买，无票"}, headers=buyer).json()
    assert po["doc_type"] == "none" and po["exception_count"] == 1
    assert client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "paid_pending"}, headers=buyer).status_code == 409
    po = client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "paid_pending", "exception_reason": "现金支付"}, headers=buyer).json()
    assert po["payment_status"] == "paid_pending" and po["exception_count"] == 2
    ok = client.post(f"/api/purchasing/pos/{pid}/payment", json={"status": "reconciled", "note": "金额按实际支付"}, headers=fin).json()
    assert ok["payment_status"] == "reconciled"


def test_PUR_quick_purchase_and_after_the_fact_approval(client, login):
    buyer, boss, wh = login("buyer"), login("boss"), login("wh")
    S = {s["code"]: s["id"] for s in master(client, wh)["skus"]}
    before = on_hand(client, wh, "PK-TRAY")
    r = client.post("/api/purchasing/pos/quick", json={"supplier": "Costco", "lines": [
        {"item_name": "垃圾袋", "unit": "盒", "qty": 3, "unit_price": 12.99},
        {"sku_id": S["PK-TRAY"], "qty": 5, "unit_price": 11}]}, headers=buyer).json()
    assert r["needs_approval"] is False
    po = client.get(f"/api/purchasing/pos/{r['id']}", headers=buyer).json()
    assert po["status"] == "received" and po["quick"] and po["payment_status"] == "paid_pending" and po["missing_doc"] is True
    assert on_hand(client, wh, "PK-TRAY") == before + 5  # stock reflects the goods in hand; PK-TRAY (包材) lot not forced on quick entry
    # over the limit: recorded, but boss must approve after the fact
    big = client.post("/api/purchasing/pos/quick", json={"supplier": "紧急维修", "category": "设备", "lines": [
        {"item_name": "压缩机紧急更换", "unit": "次", "qty": 1, "unit_price": 3500}], "doc_type": "invoice", "doc_no": "R-1"}, headers=buyer).json()
    assert big["needs_approval"] is True
    assert client.get("/api/metrics?keys=po_pending_approval", headers=boss).json()[0]["value"] >= 1
    d = client.post(f"/api/purchasing/pos/{big['id']}/approve", json={"decision": "approve"}, headers=boss).json()
    assert d["status"] == "received" and d["awaiting_approval"] is False


def test_admin_has_all_permissions(client, login):
    admin = login("admin")
    me = client.get("/api/me", headers=admin).json()
    assert {"po.approve", "po.manage", "inventory.post", "finance.reconcile", "quality.release"} <= set(me["perms"])
    pid = _new_po(client, admin, qty=1000, price=5)
    assert client.post(f"/api/purchasing/pos/{pid}/submit", headers=admin).json()["status"] == "pending_approval"
    d = client.post(f"/api/purchasing/pos/{pid}/approve", json={"decision": "approve"}, headers=admin).json()
    assert d["status"] == "approved" and d["approval"]["self"] is True  # self-approval is allowed but flagged
