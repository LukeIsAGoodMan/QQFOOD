"""ChatGPT 批量导入（SKU / 期初盘点 / 公司调入）→ 人确认后才生效。"""
from test_agent import call, connect, enable


def stock(client, h, sku, wh="WH597"):
    m = client.get("/api/inv/master", headers=h).json()
    wid = next(w["id"] for w in m["warehouses"] if w["code"] == wh)
    return next((r for r in client.get("/api/inv/stock", headers=h).json()["rows"] if r["sku_code"] == sku and r["warehouse_id"] == wid),
                {"on_hand": 0, "unverified": 0, "in_transit": 0})


def test_BAT01_skus_via_chatgpt_need_confirmation(client, login):
    ops = login("ops")
    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    md = call(client, tok, "get_master_data", {})["structuredContent"]
    assert any(w["code"] == "TOR" and w["tracked"] is False for w in md["warehouses"])
    res = call(client, tok, "propose_skus", {"source_summary": "库存表 v4", "rows": [
        {"code": "tt-dumpling", "name": "猪肉白菜水饺 1kg", "category": "成品", "base_unit": "箱", "conversions": {"袋": 12}, "safety_stock": {"WH597": 30}},
        {"code": "RM-BEEF", "name": "冷冻牛肉卷（改名）"},
        {"code": "NEW-BEEF-2", "name": "示例 冷冻牛肉卷"},          # same name as RM-BEEF → suspected duplicate
        {"code": "BAD-1", "name": "x", "category": "乱填"},           # invalid category
        {"code": "RM-SPICE", "base_unit": "包"},                      # has stock movements → cannot change base unit
    ]})["structuredContent"]
    assert res["status"] == "pending_confirmation" and "/ingest?batch=" in res["confirm_url"]
    assert res["summary"] == {"new": 1, "update": 1, "duplicate": 1, "error": 2}
    assert client.get("/api/inv/scan/TT-DUMPLING", headers=ops).status_code == 404  # nothing applied yet
    bid = res["confirm_url"].split("batch=")[1]
    b = client.get(f"/api/batches/{bid}", headers=ops).json()
    sel = [r["index"] for r in b["rows"] if r["action"] in ("new", "update")]
    r = client.post(f"/api/batches/{bid}/apply", json={"selected": sel}, headers=ops).json()
    assert r["status"] == "partial"
    assert client.get("/api/inv/scan/TT-DUMPLING", headers=ops).json()["name"] == "猪肉白菜水饺 1kg"
    assert client.post(f"/api/batches/{bid}/apply", json={"selected": sel}, headers=ops).status_code == 409  # one-shot
    assert stock(client, ops, "TT-DUMPLING")["safety_stock"] == 30


def test_BAT02_opening_stock_counted_and_estimated(client, login):
    ops = login("ops")
    _, tok = connect(client, "ops")
    res = call(client, tok, "propose_stock_counts", {"source_summary": "11/5 实盘", "as_of": "2026-11-05", "rows": [
        {"sku_code": "TT-DUMPLING", "warehouse_code": "WH597", "lot_code": "D1105", "qty": 40},
        {"sku_code": "TT-DUMPLING", "warehouse_code": "WH597", "lot_code": "D1101", "qty": 24, "unit": "袋"},   # 24 袋 = 2 箱
        {"sku_code": "TT-DUMPLING", "warehouse_code": "EXT1", "qty": 15, "counted": False},                    # 估计
        {"sku_code": "NOPE", "qty": 1},
        {"sku_code": "TT-DUMPLING", "warehouse_code": "TOR", "qty": 5},                                         # untracked
    ]})["structuredContent"]
    assert res["summary"] == {"set": 3, "error": 2}
    bid = res["confirm_url"].split("batch=")[1]
    client.post(f"/api/batches/{bid}/apply", json={"selected": [0, 1, 2, 3, 4]}, headers=ops)
    s597 = stock(client, ops, "TT-DUMPLING")
    assert s597["on_hand"] == 42 and s597["available"] == 42
    ext = stock(client, ops, "TT-DUMPLING", "EXT1")
    assert ext["unverified"] == 15 and ext["on_hand"] == 0
    # a recount sets absolute values (not increments)
    res2 = call(client, tok, "propose_stock_counts", {"source_summary": "复盘", "rows": [{"sku_code": "TT-DUMPLING", "lot_code": "D1105", "qty": 38}]})["structuredContent"]
    b2 = client.get(f"/api/batches/{res2['confirm_url'].split('batch=')[1]}", headers=ops).json()
    assert b2["rows"][0]["changes"]["实物"] == [40, 38]
    client.post(f"/api/batches/{b2['id']}/apply", json={"selected": [0]}, headers=ops)
    assert stock(client, ops, "TT-DUMPLING")["on_hand"] == 40


def test_BAT03_inbound_from_toronto(client, login):
    ops = login("ops")
    _, tok = connect(client, "ops")
    res = call(client, tok, "propose_inbound", {"from_code": "TOR", "expected_date": "2026-11-20", "note": "柜号 MSKU123",
                                                 "source_summary": "多伦多 11/4 邮件装箱单", "lines": [
                                                     {"sku_code": "TT-DUMPLING", "lot_code": "TOR-D1", "qty": 100},
                                                     {"sku_code": "GHOST", "qty": 3}]})["structuredContent"]
    assert res["summary"] == {"new": 1, "error": 1}
    before = stock(client, ops, "TT-DUMPLING")["in_transit"]
    bid = res["confirm_url"].split("batch=")[1]
    r = client.post(f"/api/batches/{bid}/apply", json={"selected": [0]}, headers=ops).json()
    assert r["result"]["transfer_code"].startswith("TR-")
    assert stock(client, ops, "TT-DUMPLING")["in_transit"] == before + 100
    assert client.get("/api/metrics?keys=pending_ingest", headers=ops).json()[0]["value"] >= 0


def test_BAT04_reject_and_staff_cannot_import(client, login):
    ops, staff = login("ops"), login("staff1")
    assert client.post("/api/batches", json={"kind": "stock_count", "rows": [{"sku_code": "TT-DUMPLING", "qty": 1}]}, headers=staff).status_code == 403
    b = client.post("/api/batches", json={"kind": "sku_upsert", "rows": [{"code": "Z1", "name": "z"}]}, headers=ops).json()
    assert client.post(f"/api/batches/{b['id']}/reject", json={"note": "不要"}, headers=ops).status_code == 200
    assert client.get(f"/api/batches/{b['id']}", headers=ops).json()["status"] == "rejected"
