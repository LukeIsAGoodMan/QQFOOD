"""客户 PO 履约 与 我们的送货单 分开；库存只按真实发货扣一次。场景取自真实单据：
- 干货 PO 623030105（$3,415）：送货单 QQUSA20261108 只送了卤水料包 6 个大统华单位（$900）+ 剁椒 1 箱（$145），11/4 签收；
- 冻货 PO 623030107（$4,234）：实送 80 箱 $3,674，奶黄流沙包 10 箱 $560 未交；
- 卤水料包：597 实物 1 箱 = 20 包，大统华 1 单位 = 10 包 → 发 6 单位只扣 3 箱；
- 2602 柜：期初录的 81 / 89 箱已是扣掉 11/4 送货后的净结存，之后补录 11/4 送货不能再扣。"""
import uuid

from app import repair
from app.db import SessionLocal
from app.models import InventoryTxn
from conftest import png


def K():
    return str(uuid.uuid4())


def M(client, h):
    return client.get("/api/inv/master", headers=h).json()


def wh(client, h, code="WH597"):
    return next(w["id"] for w in M(client, h)["warehouses"] if w["code"] == code)


def onhand(client, h, sid):
    return round(sum(r["on_hand"] for r in client.get("/api/inv/stock", headers=h).json()["rows"] if r["sku_id"] == sid), 4)


def tnt(client, h):
    return next(c for c in client.get("/api/orders/customers", headers=h).json() if c["code"] == "TNT")


def sku(client, h, name, unit="箱", conv=None):
    r = client.post("/api/inv/skus", json={"name": name, "category": "干货", "base_unit": unit, "conversions": conv or {}}, headers=h)
    return r.json()["id"] if r.status_code == 200 else next(s["id"] for s in M(client, h)["skus"] if s["name"] == name)


def count(client, h, sid, qty, lot, as_of="2026-11-05"):
    """期初盘点（ChatGPT 批次）：录的是净结存。"""
    code = next(s["code"] for s in M(client, h)["skus"] if s["id"] == sid)
    b = client.post("/api/batches", json={"kind": "stock_count", "meta": {"as_of": as_of}, "source_note": "期初实盘",
                                          "rows": [{"sku_code": code, "warehouse_code": "WH597", "lot_code": lot, "qty": qty}]}, headers=h).json()
    client.post(f"/api/batches/{b['id']}/apply", json={"selected": [0]}, headers=h)


def dry_po(client, h, po_no):
    c = tnt(client, h)
    soup, chili = sku(client, h, "FUL 卤水料包", conv={"包": 20}), sku(client, h, "FUL 剁椒")
    bean, mlt, paste = sku(client, h, "FUL 辣豆瓣酱"), sku(client, h, "FUL MLT 汤底"), sku(client, h, "FUL 辣黄豆酱")
    body = {"customer_id": c["id"], "site_id": c["sites"][0]["id"], "po_no": po_no, "po_date": "2026-10-30", "required_date": "2026-11-04", "lines": [
        {"item_no": "9505078", "description": "FUL Chili Bean Sauce", "qty": 6, "price": 125, "sku_id": bean},
        {"item_no": "9520687", "description": "FUL MLT Soup Base", "qty": 6, "price": 145, "sku_id": mlt},
        {"item_no": "9793901", "description": "FUL Soy Bean Paste With Chili", "qty": 6, "price": 125, "sku_id": paste},
        {"item_no": "9774413", "description": "FUL Marinated Soup Base", "qty": 6, "price": 150, "sku_id": soup, "factor": 0.5},
        {"item_no": "9777897", "description": "FUL Chopped Chili", "qty": 1, "price": 145, "sku_id": chili}]}
    oid = client.post("/api/orders", json=body, headers=h).json()["id"]
    return oid, soup, chili


def test_FUL01_partial_po_but_delivery_complete_and_unit_conversion(client, login):
    ops = login("ops")
    w = wh(client, ops)
    oid, soup, chili = dry_po(client, ops, "FUL-623030105")
    so = client.get(f"/api/orders/{oid}", headers=ops).json()
    assert so["total"] == 3415.0 and so["po_status"] == "open"
    for sid, lot in ((soup, "2602"), (chili, "2602")):
        client.post("/api/inv/receive", json={"idempotency_key": K(), "sku_id": sid, "warehouse_id": w, "qty": 90 if sid == chili else 84, "lot_code": lot}, headers=ops)
    L = {l["item_no"]: l for l in so["lines"]}
    assert L["9774413"]["base_per_unit"] == 0.5 and L["9774413"]["base_needed"] == 3.0
    r = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "delivery_no": "QQUSA20261108", "shipped_on": "2026-11-04", "lines": [
        {"line_id": L["9774413"]["id"], "qty": 6}, {"line_id": L["9777897"]["id"], "qty": 1}]}, headers=ops).json()
    assert onhand(client, ops, soup) == 81 and onhand(client, ops, chili) == 89                    # 6 个大统华单位只扣 3 箱
    sh = r["order"]["shipments"][0]
    assert sh["total"] == 1045.0 and sh["delivery_no"] == "QQUSA20261108" and sh["shipped_on"] == "2026-11-04"
    # 本次送货完成：PO 没发完也可以；签收单原件待补（不是“已核验”）
    assert client.post(f"/api/orders/shipments/{sh['id']}/deliver", json={}, headers=ops).status_code == 409
    o = client.post(f"/api/orders/shipments/{sh['id']}/deliver", json={"delivered_on": "2026-11-04", "received_by": "Chino Hills 收货员签字", "pod_pending": True}, headers=ops).json()
    s0 = o["shipments"][0]
    assert s0["status"] == "delivered" and s0["status_label"] == "已签收完成" and s0["delivered_on"] == "2026-11-04"
    assert s0["pod_status"] == "to_upload"
    assert o["po_status"] == "partial" and o["latest_delivery"]["status"] == "delivered"
    assert o["shipped_amount"] == 1045.0 and o["delivered_amount"] == 1045.0 and o["remaining_amount"] == 2370.0 and o["total"] == 3415.0
    # 再确认一次：不变、不扣库存
    again = client.post(f"/api/orders/shipments/{sh['id']}/deliver", json={"delivered_on": "2026-11-05", "pod_pending": True, "received_by": "x"}, headers=ops).json()
    assert again["already_delivered"] and again["shipments"][0]["delivered_on"] == "2026-11-04"
    assert onhand(client, ops, soup) == 81 and onhand(client, ops, chili) == 89
    # 后来传上签收单 → 待核验（不是已核验）
    client.post("/api/evidence/upload", files={"file": ("pod.png", png(1104), "image/png")},
                data={"target_type": "shipment", "target_id": sh["id"], "purpose": "签收单"}, headers=ops)
    assert client.get(f"/api/orders/{oid}", headers=ops).json()["shipments"][0]["pod_status"] == "pending"


def test_FUL02_balance_awaiting_new_po_keeps_history(client, login):
    ops = login("ops")
    so = next(o for o in client.get("/api/orders", headers=ops).json() if o["po_no"] == "FUL-623030105")
    assert client.post(f"/api/orders/{so['id']}/balance", json={"disposition": "awaiting_new_po"}, headers=ops).status_code == 400  # 要原因
    o = client.post(f"/api/orders/{so['id']}/balance", json={"disposition": "awaiting_new_po", "reason": "T&T 要求剩余商品重新开 PO",
                                                               "customer_ref": "11/6 Kevin 邮件"}, headers=ops).json()
    assert o["po_status"] == "closed_balance" and o["status"] == "closed" and not o["overdue"]
    assert o["total"] == 3415.0 and o["remaining_amount"] == 0 and o["closed_amount"] == 2370.0     # 原 PO 数量金额不变
    L = {l["item_no"]: l for l in o["lines"]}
    assert L["9505078"]["qty_ordered"] == 6 and L["9505078"]["qty_shipped"] == 0 and L["9505078"]["balance_label"] == "等客户新 PO"
    assert L["9505078"]["balance_ref"] == "11/6 Kevin 邮件"
    s0 = o["shipments"][0]
    assert s0["status"] == "delivered" and s0["pod_status"] == "pending"                           # 送货单和凭证不受影响
    # 撤销处置 → 又回到欠着
    o = client.post(f"/api/orders/{so['id']}/balance", json={"disposition": ""}, headers=ops).json()
    assert o["po_status"] == "partial" and o["remaining_amount"] == 2370.0
    o = client.post(f"/api/orders/{so['id']}/balance", json={"disposition": "backordered", "reason": "等 2603 柜到货"}, headers=ops).json()
    assert o["po_status"] == "partial" and not o["overdue"] and o["backordered"]                     # 补货中：仍欠，但不算逾期催单


def test_FUL03_multiple_shipments_and_frozen_po(client, login):
    ops = login("ops")
    w = wh(client, ops)
    c = tnt(client, ops)
    items = [("Chinese Donut", 20, 40.0), ("Mango Pomelo Sago Steamed Bun", 10, 50.0), ("Taro & Sweet Potato Paste Balls", 10, 46.4),
             ("Fried Sesame Ball", 40, 47.75), ("Custard Lava Steamed Bun", 10, 56.0)]
    ids = {n: sku(client, ops, "FUL " + n) for n, _, _ in items}
    for n, q, _ in items[:4]:
        client.post("/api/inv/receive", json={"idempotency_key": K(), "sku_id": ids[n], "warehouse_id": w, "qty": q, "lot_code": "F1"}, headers=ops)
    body = {"customer_id": c["id"], "site_id": c["sites"][0]["id"], "po_no": "FUL-623030107", "po_date": "2026-10-30", "required_date": "2026-11-04",
            "lines": [{"description": "FUL " + n, "qty": q, "price": p, "sku_id": ids[n]} for n, q, p in items]}
    oid = client.post("/api/orders", json=body, headers=ops).json()["id"]
    so = client.get(f"/api/orders/{oid}", headers=ops).json()
    assert so["total"] == 4234.0
    L = {l["description"][4:]: l["id"] for l in so["lines"]}
    # 两趟送：先 50 箱、再 30 箱
    r1 = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": L["Chinese Donut"], "qty": 20}, {"line_id": L["Fried Sesame Ball"], "qty": 30}]}, headers=ops).json()
    r2 = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": L["Mango Pomelo Sago Steamed Bun"], "qty": 10},
                                                                                         {"line_id": L["Taro & Sweet Potato Paste Balls"], "qty": 10}, {"line_id": L["Fried Sesame Ball"], "qty": 10}]}, headers=ops).json()
    for sh in r2["order"]["shipments"]:
        client.post(f"/api/orders/shipments/{sh['id']}/deliver", json={"pod_pending": True, "received_by": "签字"}, headers=ops)
    o = client.get(f"/api/orders/{oid}", headers=ops).json()
    assert len(o["shipments"]) == 2 and all(s["status"] == "delivered" for s in o["shipments"])
    assert o["po_status"] == "partial" and o["shipped_amount"] == 3674.0 and o["remaining_amount"] == 560.0
    # 没有库存的奶黄流沙包不能“发货完成”
    r = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": L["Custard Lava Steamed Bun"], "qty": 10}]}, headers=ops)
    assert r.status_code == 409 and "库存不足" in r.text


def test_FUL04_unshipped_po_not_auto_marked(client, login):
    ops = login("ops")
    c = tnt(client, ops)
    papa = sku(client, ops, "FUL 爸爸鸡辣椒粉")
    oid = client.post("/api/orders", json={"customer_id": c["id"], "site_id": c["sites"][0]["id"], "po_no": "FUL-623030106", "required_date": "2026-11-04",
                                           "lines": [{"item_no": "9639121", "description": "FUL Chili Powder Papa Chicken", "qty": 6, "price": 145, "sku_id": papa}]}, headers=ops).json()["id"]
    o = client.get(f"/api/orders/{oid}", headers=ops).json()
    assert o["po_status"] == "open" and o["overdue"] and not o["shipments"] and o["latest_delivery"] is None


def test_FUL05_historical_shipment_does_not_deduct_again(client, login):
    ops = login("ops")
    oid, soup, chili = dry_po(client, ops, "FUL-623030199")
    count(client, ops, soup, 81, "2602-H")      # 11/5 期初：已经是扣掉 11/4 送货后的净结存
    count(client, ops, chili, 89, "2602-H")
    before = (onhand(client, ops, soup), onhand(client, ops, chili))
    L = {l["item_no"]: l for l in client.get(f"/api/orders/{oid}", headers=ops).json()["lines"]}
    body = {"idempotency_key": K(), "shipped_on": "2026-11-04", "delivery_no": "QQUSA-H1", "historical": True,
            "lines": [{"line_id": L["9774413"]["id"], "qty": 6}, {"line_id": L["9777897"]["id"], "qty": 1}]}
    assert client.post(f"/api/orders/{oid}/ship", json=body, headers=ops).status_code == 400          # 要写原因
    body["historical_reason"] = "11/5 期初 81 / 89 箱已是扣掉 11/4 送货后的净结存"
    o = client.post(f"/api/orders/{oid}/ship", json=body, headers=ops).json()["order"]
    assert (onhand(client, ops, soup), onhand(client, ops, chili)) == before
    sh = o["shipments"][0]
    assert sh["inventory_effect"] == "historical" and o["lines"][3]["qty_shipped"] == 6 and o["po_status"] == "partial"
    o = client.post(f"/api/orders/shipments/{sh['id']}/deliver", json={"delivered_on": "2026-11-04", "pod_pending": True, "received_by": "签字"}, headers=ops).json()
    assert (onhand(client, ops, soup), onhand(client, ops, chili)) == before and o["shipments"][0]["status"] == "delivered"


def test_FUL06_repair_double_deducted_backfill(client, login):
    """真实情况：补录 11/4 送货时系统又扣了一次 → 预览找出来 → 确认后冲销，送货单 / PO / 凭证都不动。"""
    ops, boss = login("ops"), login("boss")
    oid, soup, chili = dry_po(client, ops, "FUL-623030188")
    count(client, ops, soup, 81, "2602-R")
    count(client, ops, chili, 89, "2602-R")
    base_soup, base_chili = onhand(client, ops, soup), onhand(client, ops, chili)
    L = {l["item_no"]: l for l in client.get(f"/api/orders/{oid}", headers=ops).json()["lines"]}
    o = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": L["9774413"]["id"], "qty": 6}, {"line_id": L["9777897"]["id"], "qty": 1}]},
                    headers=ops).json()["order"]       # 当时没选“历史补录”，又扣了 3 + 1
    sh = o["shipments"][-1]
    assert onhand(client, ops, soup) == base_soup - 3 and onhand(client, ops, chili) == base_chili - 1
    # 更正业务日期（系统录入时间保留）
    o = client.patch(f"/api/orders/shipments/{sh['id']}", json={"shipped_on": "2026-11-04", "delivery_no": "QQUSA20261108"}, headers=ops).json()
    assert o["shipments"][-1]["shipped_on"] == "2026-11-04" and o["shipments"][-1]["shipped_at"]
    assert client.get("/api/admin/repair/historical-shipments", headers=ops).status_code == 403
    pv = client.get("/api/admin/repair/historical-shipments", headers=boss).json()
    hit = next(x for x in pv["shipments"] if x["shipment_id"] == sh["id"])
    assert hit["suspect"] and {l["on_hand_after_fix"] for l in hit["lines"]} == {base_soup, base_chili}
    assert client.post("/api/admin/repair/historical-shipments", json={"shipment_ids": [sh["id"]], "reason": "x", "confirm": "错"}, headers=boss).status_code == 400
    db = SessionLocal()
    try:
        n_txn = db.query(InventoryTxn).count()
    finally:
        db.close()
    r = client.post("/api/admin/repair/historical-shipments", json={"shipment_ids": [sh["id"]], "reason": "期初已是净结存", "confirm": "确认修复"}, headers=boss).json()
    assert r["fixed"] == [sh["code"]] and r["backup"]
    assert onhand(client, ops, soup) == base_soup and onhand(client, ops, chili) == base_chili   # 81 / 89
    r = client.post("/api/admin/repair/historical-shipments", json={"shipment_ids": [sh["id"]], "reason": "再来一次", "confirm": "确认修复"}, headers=boss).json()
    assert r["fixed"] == [] and onhand(client, ops, soup) == base_soup                              # 不会修两次
    db = SessionLocal()
    try:
        assert db.query(InventoryTxn).count() == n_txn + 2                                          # 只追加冲销，不删
    finally:
        db.close()
    o = client.get(f"/api/orders/{oid}", headers=ops).json()
    assert o["shipments"][-1]["inventory_effect"] == "reversed_historical" and o["lines"][3]["qty_shipped"] == 6


def test_FUL07_migration_keeps_balances(client, login):
    """加字段的迁移不动库存：重跑一遍迁移前后结存一致。"""
    from app.main import _ensure_columns
    ops = login("ops")
    before = {(r["sku_id"], r["warehouse_id"]): r["on_hand"] for r in client.get("/api/inv/stock", headers=ops).json()["rows"]}
    _ensure_columns()
    after = {(r["sku_id"], r["warehouse_id"]): r["on_hand"] for r in client.get("/api/inv/stock", headers=ops).json()["rows"]}
    assert before == after
    assert repair.CONFIRM == "确认修复"


def test_FUL08_change_factor_for_future_shipments(client, login):
    ops = login("ops")
    oid, soup, chili = dry_po(client, ops, "FUL-623030177")
    L = {l["item_no"]: l for l in client.get(f"/api/orders/{oid}", headers=ops).json()["lines"]}
    o = client.post(f"/api/orders/lines/{L['9505078']['id']}/factor", json={"factor": 0.25, "reason": "大统华 1 单位 = 5 包，我们 1 箱 20 包"}, headers=ops).json()
    assert next(l for l in o["lines"] if l["item_no"] == "9505078")["base_per_unit"] == 0.25
    assert client.post(f"/api/orders/lines/{L['9505078']['id']}/factor", json={"factor": 0}, headers=ops).status_code == 400
