"""切配生产：备料（出成率 + 供应商箱规）、生产记录（损耗）、称重箱直接发货。用户举的例子：下周二 300 lb 鸡丁 + 300 lb 咕咾肉。"""
import uuid


def K():
    return str(uuid.uuid4())


def ids(client, h):
    m = client.get("/api/inv/master", headers=h).json()
    return {s["code"]: s["id"] for s in m["skus"]}, {w["code"]: w["id"] for w in m["warehouses"]}


def test_PRD_plan_rounds_up_to_supplier_cases_then_run_with_loss(client, login):
    ops = login("ops")
    S, W = ids(client, ops)
    tnt = next(c for c in client.get("/api/orders/customers", headers=ops).json() if c["code"] == "TNT")
    oid = client.post("/api/orders", json={"customer_id": tnt["id"], "site_id": tnt["sites"][0]["id"], "po_no": "CH-CK", "po_date": "2026-11-06",
                                           "required_date": "2026-11-10", "lines": [
                                               {"description": "Chicken Leg-Diced", "unit": "lb", "qty": 300, "price": 3.60, "sku_id": S["FG-CHK-DICE"]},
                                               {"description": "Pork Shoulder Butt-Diced", "unit": "lb", "qty": 300, "price": 3.68, "sku_id": S["FG-PORK-DICE"]}]},
                      headers=ops).json()["id"]
    plan = client.get(f"/api/production/plan?order_id={oid}", headers=ops).json()
    mats = {m["sku"]["code"]: m for m in plan["materials"]}
    # 300 / 0.95 = 315.8 lb → 40 lb cases → 8 cases = 320; 65 lb cases → 5 cases = 325 (the user's numbers)
    assert mats["RM-CHK-LEG"]["suggest_cases"] == 8 and mats["RM-CHK-LEG"]["suggest_qty"] == 320
    assert mats["RM-PORK-BUTT"]["suggest_cases"] == 5 and mats["RM-PORK-BUTT"]["suggest_qty"] == 325
    outs = {o["sku"]["code"]: o for o in plan["outputs"]}
    assert outs["FG-CHK-DICE"]["boxes_suggested"] == 6  # 300 / 50
    # one click: purchase draft for the raw meat
    po = client.post("/api/production/plan/purchase", json={"supplier": "鸡肉供应商", "items": [{"sku_id": S["RM-CHK-LEG"], "qty": 320, "cases": 8}]}, headers=ops).json()
    pod = client.get(f"/api/purchasing/pos/{po['id']}", headers=ops).json()
    assert pod["lines"][0]["qty_ordered"] == 320 and "8 箱 × 40" in pod["lines"][0]["item_name"]
    # supplier changes to bigger cases → just change the parameter
    client.patch(f"/api/production/skus/{S['RM-PORK-BUTT']}", json={"purchase_pack": 80}, headers=ops)
    plan2 = client.get(f"/api/production/plan?order_id={oid}", headers=ops).json()
    assert {m["sku"]["code"]: m for m in plan2["materials"]}["RM-PORK-BUTT"]["suggest_cases"] == 4  # 315.8 / 80
    # receive 320 lb chicken (8 cases), then produce: all raw consumed, 6 boxes ≈ 300 lb, remainder = loss
    client.post("/api/inv/receive", json={"idempotency_key": K(), "sku_id": S["RM-CHK-LEG"], "warehouse_id": W["WH597"], "qty": 320, "lot_code": "CHK-R1"}, headers=ops)
    lots = client.get(f"/api/production/inputs/{S['RM-CHK-LEG']}", headers=ops).json()
    r = client.post("/api/production/runs", json={"idempotency_key": K(), "output_sku_id": S["FG-CHK-DICE"], "lot_code": "CK1107", "order_id": oid,
                                                  "inputs": [{"lot_id": lots[0]["lot_id"], "sku_id": S["RM-CHK-LEG"]}], "leftover": "loss",
                                                  "box_weights": [50.2, 49.8, 50.0, 50.4, 49.6, 50.1], "release": True}, headers=login("admin")).json()
    run = r["run"]
    assert run["consumed_qty"] == 320 and run["output_qty"] == 300.1 and run["loss_qty"] == 19.9 and run["yield_pct"] == 93.8
    assert run["expected_yield_pct"] == 95.0 and run["loss_alert"] is False and run["quality_status"] == "released"
    assert len(r["boxes"]) == 6
    raw_left = client.get(f"/api/production/inputs/{S['RM-CHK-LEG']}", headers=ops).json()
    assert raw_left == []  # remainder written off as production loss
    # boxes from the run ship straight to the order
    so = client.get(f"/api/orders/{oid}", headers=ops).json()
    chk = next(l for l in so["lines"] if l["sku_code"] == "FG-CHK-DICE")
    shipped = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": chk["id"], "box_ids": [b["id"] for b in r["boxes"]]}]}, headers=ops).json()
    assert shipped["order"]["lines"][0]["qty_shipped"] == 300.1
    # trace: diced chicken lot ← raw chicken lot
    ck = next(l["id"] for l in client.get("/api/inv/master", headers=ops).json()["lots"] if l["lot_code"] == "CK1107")
    back = client.get(f"/api/inv/trace/{ck}", headers=ops).json()["backward"]
    assert back[0]["lot_code"] == "CHK-R1" and back[0]["qty"] == 320


def test_PRD_keep_leftover_and_loss_alert(client, login):
    ops = login("ops")
    S, W = ids(client, ops)
    client.post("/api/inv/receive", json={"idempotency_key": K(), "sku_id": S["RM-PORK-BUTT"], "warehouse_id": W["WH597"], "qty": 325, "lot_code": "PORK-R1"}, headers=ops)
    lot = client.get(f"/api/production/inputs/{S['RM-PORK-BUTT']}", headers=ops).json()[0]
    r = client.post("/api/production/runs", json={"idempotency_key": K(), "output_sku_id": S["FG-PORK-DICE"], "lot_code": "PK1107",
                                                  "inputs": [{"lot_id": lot["lot_id"], "sku_id": S["RM-PORK-BUTT"], "qty": 300}], "leftover": "keep",
                                                  "box_weights": [50, 50, 50, 50, 50, 20]}, headers=ops).json()["run"]
    # 300 in, 270 out → 90% vs expected 95% → flagged; 25 lb raw stays in stock; batch pending release (not ticked)
    assert r["loss_qty"] == 30 and r["yield_pct"] == 90.0 and r["loss_alert"] is True and r["quality_status"] == "pending"
    assert client.get(f"/api/production/inputs/{S['RM-PORK-BUTT']}", headers=ops).json()[0]["qty"] == 25
    # output larger than input is refused
    bad = client.post("/api/production/runs", json={"idempotency_key": K(), "output_sku_id": S["FG-PORK-DICE"], "lot_code": "PK-BAD",
                                                    "inputs": [{"lot_id": lot["lot_id"], "sku_id": S["RM-PORK-BUTT"], "qty": 10}], "leftover": "keep",
                                                    "box_weights": [50]}, headers=ops)
    assert bad.status_code == 409


def test_PRD_recipe_and_unit_change_rules(client, login):
    ops = login("ops")
    S, _ = ids(client, ops)
    # without release permission the run is refused before any stock moves
    lots_before = client.get(f"/api/production/inputs/{S['RM-PORK-BUTT']}", headers=ops).json()
    no = client.post("/api/production/runs", json={"idempotency_key": K(), "output_sku_id": S["FG-PORK-DICE"], "lot_code": "PK-NOPERM", "release": True,
                                                   "inputs": [{"lot_id": lots_before[0]["lot_id"], "sku_id": S["RM-PORK-BUTT"]}], "box_weights": [10]}, headers=ops)
    assert no.status_code == 403 and client.get(f"/api/production/inputs/{S['RM-PORK-BUTT']}", headers=ops).json() == lots_before
    r = client.put(f"/api/production/recipes/{S['FG-PORK-DICE']}", json={"lines": [{"sku_id": S["RM-PORK-BUTT"], "yield_pct": 92}]}, headers=ops).json()
    assert r["expected_yield_pct"] == 92.0
    assert client.put(f"/api/production/recipes/{S['FG-PORK-DICE']}", json={"lines": [{"sku_id": S["RM-PORK-BUTT"], "yield_pct": 120}]}, headers=ops).status_code == 400
    # base unit can switch to 磅 only while the SKU has no stock movements
    assert client.patch(f"/api/production/skus/{S['RM-SPICE']}", json={"base_unit": "磅"}, headers=ops).status_code == 409
    new = client.post("/api/inv/skus", json={"code": "TMP-MEAT", "name": "临时肉", "base_unit": "箱"}, headers=ops).json()
    out = client.patch(f"/api/production/skus/{new['id']}", json={"base_unit": "磅", "ship_pack": 30}, headers=ops).json()
    assert out["catch_weight"] is True and out["ship_pack"] == 30


def test_orders_upgrade_on_existing_db(client, login):
    """模拟线上已有的库：去掉升级标记再跑升级 → 订单页换模块（旧的隐藏）、生产页切配生产在前并加补打标签、大统华客户不重复。"""
    from app.db import SessionLocal
    from app.models import AppSetting, Customer
    from app.seed import upgrade_orders
    db = SessionLocal()
    db.delete(db.get(AppSetting, "migration:orders_v1"))
    db.commit()
    db.close()
    upgrade_orders()
    upgrade_orders()  # idempotent
    h = login("ops")
    pages = {p["name"]: p["id"] for p in client.get("/api/pages", headers=h).json()}
    orders = client.get(f"/api/pages/{pages['订单与配送']}", headers=h).json()["layout"]["modules"]
    assert orders[3]["type"] == "orders" and all(m["hidden"] for m in orders[5:])
    prod = [m["type"] for m in client.get(f"/api/pages/{pages['生产']}", headers=h).json()["layout"]["modules"]]
    assert prod.index("production") < prod.index("boxes")
    db = SessionLocal()
    assert db.query(Customer).filter_by(code="TNT").count() == 1
    db.close()
