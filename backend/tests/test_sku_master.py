"""商品主档：手机上建 / 改 / 停用商品；同一个东西建成两个商品时合并（库存转过去，旧名记成旧称）。
场景取自真实情况：国内采购叫“三樱辣椒段”，卖给大统华叫“剁椒”；CHIYUE 辣椒粉 = 爸爸鸡辣椒粉。"""
import uuid

from test_agent import call, connect, enable


def M(client, h, inactive=False):
    return client.get(f"/api/inv/master{'?include_inactive=1' if inactive else ''}", headers=h).json()


def sku(client, h, name, inactive=False):
    return next((s for s in M(client, h, inactive)["skus"] if s["name"] == name), None)


def wh(client, h, code="WH597"):
    return next(w["id"] for w in M(client, h)["warehouses"] if w["code"] == code)


def onhand(client, h, sid):
    return round(sum(r["on_hand"] for r in client.get("/api/inv/stock", headers=h).json()["rows"] if r["sku_id"] == sid), 3)


def test_SKU01_create_edit_deactivate(client, login):
    ops, staff = login("ops"), login("staff1")
    assert client.post("/api/inv/skus", json={"name": "测试 剁椒"}, headers=staff).status_code == 403
    r = client.post("/api/inv/skus", json={"name": "测试 剁椒", "category": "干货", "tnt_item_no": "877897", "purchase_pack": 12}, headers=ops)
    assert r.status_code == 200 and r.json()["code"].startswith("QQ-")
    s = sku(client, ops, "测试 剁椒")
    assert s["tnt_item_no"] == "877897" and s["purchase_pack"] == 12 and s["category"] == "干货" and s["active"]
    assert client.post("/api/inv/skus", json={"name": "测试剁椒"}, headers=ops).status_code == 409             # 同名（忽略空格）
    assert client.post("/api/inv/skus", json={"name": "别的", "tnt_item_no": "877897"}, headers=ops).status_code == 409  # Item# 已被占用
    # 改：品名、内部编号、Item#（录错了 → 替换）、箱规
    r = client.patch(f"/api/inv/skus/{s['id']}", json={"name": "测试 剁椒 1kg", "code": "duojiao-1", "tnt_item_no": "877898", "ship_pack": 10}, headers=ops).json()
    assert r["name"] == "测试 剁椒 1kg" and r["code"] == "DUOJIAO-1" and r["tnt_item_no"] == "877898" and r["ship_pack"] == 10
    # 有库存后不能改单位，给出做法
    client.post("/api/inv/receive", json={"idempotency_key": str(uuid.uuid4()), "sku_id": s["id"], "warehouse_id": wh(client, ops), "qty": 5, "lot_code": "DJ1"}, headers=ops)
    r = client.patch(f"/api/inv/skus/{s['id']}", json={"base_unit": "磅"}, headers=ops)
    assert r.status_code == 409 and "盘点过去" in r.text
    # 停用：有库存要写原因
    assert client.patch(f"/api/inv/skus/{s['id']}", json={"active": False}, headers=ops).status_code == 409
    assert client.patch(f"/api/inv/skus/{s['id']}", json={"active": False, "reason": "测试"}, headers=ops).json()["active"] is False
    assert sku(client, ops, "测试 剁椒 1kg") is None and sku(client, ops, "测试 剁椒 1kg", inactive=True)  # 选择列表里不出现
    assert client.patch(f"/api/inv/skus/{s['id']}", json={"active": True}, headers=ops).json()["active"] is True


def test_SKU02_merge_moves_stock_and_keeps_old_name(client, login):
    ops = login("ops")
    w = wh(client, ops)
    a = client.post("/api/inv/skus", json={"name": "三樱辣椒段", "category": "干货"}, headers=ops).json()["id"]
    b = client.post("/api/inv/skus", json={"name": "剁椒", "category": "干货", "tnt_item_no": "777897"}, headers=ops).json()["id"]
    client.post("/api/inv/receive", json={"idempotency_key": str(uuid.uuid4()), "sku_id": a, "warehouse_id": w, "qty": 30, "lot_code": "2602-A"}, headers=ops)
    client.post("/api/inv/receive", json={"idempotency_key": str(uuid.uuid4()), "sku_id": b, "warehouse_id": w, "qty": 10, "lot_code": "TT-1"}, headers=ops)
    client.post("/api/aliases", json={"sku_id": a, "party_kind": "supplier", "party": "国内供应商", "code": "SY-01", "name": "三樱辣椒段 5kg"}, headers=ops)
    us = client.get(f"/api/inv/skus/{a}/usage", headers=ops).json()
    assert us["has_stock"] and us["stock"]["on_hand"] == 30
    assert client.post(f"/api/inv/skus/{a}/merge", json={"into_id": b, "reason": ""}, headers=ops).status_code == 400  # 要写原因
    assert client.post(f"/api/inv/skus/{a}/merge", json={"into_id": a, "reason": "x"}, headers=ops).status_code == 409
    r = client.post(f"/api/inv/skus/{a}/merge", json={"into_id": b, "reason": "老板确认：国内采购名和大统华品名是同一个东西"}, headers=ops).json()
    assert r["moved_balances"] == 1 and r["into"]["name"] == "剁椒"
    assert onhand(client, ops, b) == 40 and onhand(client, ops, a) == 0
    assert sku(client, ops, "三樱辣椒段") is None                       # 名字腾出来了，选择列表里也没有了
    old = next(s for s in M(client, ops, True)["skus"] if s["id"] == a)
    assert old["active"] is False and old["merged_into"] == b
    al = client.get(f"/api/aliases?sku_id={b}", headers=ops).json()
    assert any(x["name"] == "三樱辣椒段" for x in al) and any(x["code"] == "SY-01" for x in al)  # 旧名 + 原对照都挂到剁椒
    lots = [l["lot_code"] for l in M(client, ops)["lots"] if l["sku_id"] == b]
    assert "2602-A" in lots                                              # Lot 跟过去，可追溯
    # 以后国内单据上写旧名，自动认成剁椒
    bt = client.post("/api/batches", json={"kind": "stock_count", "rows": [{"name": "三樱辣椒段", "warehouse_code": "WH597", "lot_code": "2602-A", "qty": 30}]}, headers=ops).json()
    row = client.get(f"/api/batches/{bt['id']}", headers=ops).json()["rows"][0]
    assert row["sku_id"] == b, row
    assert client.patch(f"/api/inv/skus/{a}", json={"active": True}, headers=ops).status_code == 409  # 已合并的不能再启用


def test_SKU03_chatgpt_proposes_merge(client, login):
    ops = login("ops")
    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    client.post("/api/inv/skus", json={"name": "CHIYUE 辣椒粉", "category": "干货"}, headers=ops)
    client.post("/api/inv/skus", json={"name": "爸爸鸡辣椒粉", "category": "干货", "tnt_item_no": "639121"}, headers=ops)
    res = call(client, tok, "propose_sku_merge", {"source_summary": "老板确认 CHIYUE = 爸爸鸡辣椒粉", "rows": [
        {"from": "CHIYUE 辣椒粉", "into": "639121", "reason": "老板确认是同一个东西"},       # into 用大统华 Item# 也认
        {"from": "三樱辣椒段 5kg 国内新叫法", "into": "剁椒", "reason": "x"}]})["structuredContent"]
    assert res["summary"] == {"merge": 1, "error": 1}
    err = next(e for e in res["errors"] if "国内新叫法" in e["key"])
    assert any("商品对照" in m for m in err["problems"])                  # 只是叫法不同 → 提示记对照，不用合并
    bid = res["confirm_url"].split("batch=")[1]
    b = client.get(f"/api/batches/{bid}", headers=ops).json()
    assert client.get("/api/inv/master", headers=ops).json()["skus"] and sku(client, ops, "CHIYUE 辣椒粉")  # 还没生效
    r = client.post(f"/api/batches/{bid}/apply", json={"selected": [x["index"] for x in b["rows"] if x["action"] == "merge"]}, headers=ops).json()
    assert r["status"] == "partial" and sku(client, ops, "CHIYUE 辣椒粉") is None
    md = call(client, tok, "get_master_data", {})["structuredContent"]
    assert not any("已并入" in s["name"] for s in md["skus"])  # ChatGPT 看不到已合并的
