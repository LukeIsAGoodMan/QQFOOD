"""采购小票：拍照发给 ChatGPT → 手机确认 → 采购记录 + 入库；香料一部分自用一部分转卖分开记；买回来再决定的可以转用途。"""
from conftest import png
from test_agent import call, connect, enable


def M(client, h, inactive=False):
    return client.get(f"/api/inv/master{'?include_inactive=1' if inactive else ''}", headers=h).json()


def onhand(client, h, sid):
    return round(sum(r["on_hand"] for r in client.get("/api/inv/stock", headers=h).json()["rows"] if r["sku_id"] == sid), 3)


RECEIPT = {"supplier": "Costco", "purchase_date": "2026-11-12", "receipt_no": "C-88123", "tax": 1.5, "total": 61.5, "payment_method": "公司卡"}


def test_RCP01_receipt_to_purchase_and_stock(client, login):
    ops = login("ops")
    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    sp = client.post("/api/inv/skus", json={"name": "五香粉", "category": "干货", "base_unit": "袋", "tnt_item_no": "530001"}, headers=ops).json()
    s = next(x for x in M(client, ops)["skus"] if x["id"] == sp["id"])
    assert s["usage"] == "resale"                                                     # 干货默认转卖
    res = call(client, tok, "propose_purchase_receipt", {"meta": RECEIPT, "source_summary": "11/12 Costco 小票", "proof_note": "小票照片已让用户上传", "lines": [
        {"party_name": "FIVE SPICE PWD", "party_code": "88001", "qty": 10, "unit": "袋", "unit_price": 5, "amount": 50, "sku_code": s["code"], "self_qty": 4},
        {"party_name": "NITRILE GLOVES", "qty": 1, "amount": 10, "usage": "expense"},
        {"party_name": "KS MYSTERY", "qty": 2, "amount": 0}]})["structuredContent"]
    assert res["summary"] == {"stock": 1, "expense": 1, "unmapped": 1} and "/u/" in res["upload_url"]
    bid = res["confirm_url"].split("batch=")[1]
    rows = client.get(f"/api/batches/{bid}", headers=ops).json()["rows"]
    assert rows[0]["lot_code"] == "COSTCO-261112" and any("自动编" in m for m in rows[0]["messages"])
    assert "自用 4" in rows[0]["changes"]["入库"][1]
    # 小票照片先传到批次
    assert client.post(f"/api/upload-link/{res['upload_url'].split('/u/')[1]}", files={"file": ("costco.png", png(8812), "image/png")}).status_code == 200
    # 确认：不认识的那行标费用
    r = client.post(f"/api/batches/{bid}/apply", json={"selected": [0, 1, 2], "overrides": {"2": {"expense": True}}}, headers=ops).json()
    assert r["status"] == "applied" and r["result"]["po_code"].startswith("PO")
    po = client.get(f"/api/purchasing/pos/{r['result']['po_id']}", headers=ops).json()
    assert po["quick"] and po["status"] == "received" and po["purchase_date"] == "2026-11-12" and po["payment_method"] == "company_card"
    assert po["invoice_no"] == "C-88123" and po["invoice_amount"] == 61.5
    assert len(po["lines"]) == 4                                                       # 转卖 6 + 自用 4 + 手套 + 不认识的
    twin = next(x for x in M(client, ops)["skus"] if x["name"] == "五香粉（自用）")
    assert twin["usage"] == "production" and twin["twin_id"] == s["id"]
    assert onhand(client, ops, s["id"]) == 6 and onhand(client, ops, twin["id"]) == 4
    assert any(e["purpose"] == "小票" for e in client.get(f"/api/evidence?target_type=po&target_id={po['id']}", headers=ops).json())
    # 记住了 Costco 的叫法：下一张小票不给 sku_code 也认得
    res2 = call(client, tok, "propose_purchase_receipt", {"meta": {**RECEIPT, "receipt_no": "C-88200", "purchase_date": "2026-11-19", "total": 25, "tax": 0},
                                                          "source_summary": "11/19 Costco", "lines": [{"party_name": "FIVE SPICE PWD", "party_code": "88001", "qty": 5, "unit_price": 5, "amount": 25}]})["structuredContent"]
    assert res2["summary"] == {"stock": 1}


def test_RCP02_duplicates_totals_and_personal_payment(client, login):
    ops = login("ops")
    _, tok = connect(client, "ops")
    res = call(client, tok, "propose_purchase_receipt", {"meta": RECEIPT, "source_summary": "同一张小票又发了一次", "lines": [
        {"party_name": "FIVE SPICE PWD", "party_code": "88001", "qty": 10, "unit_price": 5, "amount": 50}]})["structuredContent"]
    assert res["possible_duplicates"]                                                  # 同一个小票号
    rows = client.get(f"/api/batches/{res['confirm_url'].split('batch=')[1]}", headers=ops).json()["rows"]
    assert rows[0]["suspect"] and any("金额对不上" in m for m in rows[0]["messages"])  # 50 + 1.5 ≠ 61.5
    res = call(client, tok, "propose_purchase_receipt", {"meta": {"supplier": "99 Ranch", "purchase_date": "2026-11-12", "total": 8, "payment_method": "个人垫付"},
                                                         "source_summary": "垫付", "lines": [{"party_name": "GINGER", "qty": 2, "unit": "lb", "amount": 8, "usage": "expense"}]})["structuredContent"]
    assert res["summary"] == {"error": 1} and "谁付的" in str(res["errors"])


def test_RCP03_convert_usage_later(client, login):
    ops = login("ops")
    s = next(x for x in M(client, ops)["skus"] if x["name"] == "五香粉")
    twin = next(x for x in M(client, ops)["skus"] if x["name"] == "五香粉（自用）")
    w = next(x["id"] for x in M(client, ops)["warehouses"] if x["code"] == "WH597")
    lot = next(l["id"] for l in M(client, ops)["lots"] if l["sku_id"] == s["id"] and l["lot_code"] == "COSTCO-261112")
    assert client.post(f"/api/inv/skus/{s['id']}/convert-usage", json={"warehouse_id": w, "lot_id": lot, "qty": 99}, headers=ops).status_code == 409
    r = client.post(f"/api/inv/skus/{s['id']}/convert-usage", json={"warehouse_id": w, "lot_id": lot, "qty": 2, "reason": "这周腌料不够"}, headers=ops).json()
    assert r["to"] == "五香粉（自用）"
    assert onhand(client, ops, s["id"]) == 4 and onhand(client, ops, twin["id"]) == 6
    assert any(l["lot_code"] == "COSTCO-261112" for l in M(client, ops)["lots"] if l["sku_id"] == twin["id"])  # Lot 跟过去
    # 没有对应商品的，转用途时自动建
    p = client.post("/api/inv/skus", json={"name": "花椒粒", "category": "干货", "base_unit": "袋"}, headers=ops).json()
    import uuid
    client.post("/api/inv/receive", json={"idempotency_key": str(uuid.uuid4()), "sku_id": p["id"], "warehouse_id": w, "qty": 3, "lot_code": "HJ1"}, headers=ops)
    lot2 = next(l["id"] for l in M(client, ops)["lots"] if l["sku_id"] == p["id"])
    assert client.post(f"/api/inv/skus/{p['id']}/convert-usage", json={"warehouse_id": w, "lot_id": lot2, "qty": 1}, headers=ops).json()["to"] == "花椒粒（自用）"
