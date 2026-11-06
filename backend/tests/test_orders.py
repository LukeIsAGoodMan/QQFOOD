"""客户订单（大统华 PO）与发货：按箱（系统 PO）与称重（中央厨房 PO）两种。"""
import uuid

from conftest import png
from test_agent import call, connect, enable


def K():
    return str(uuid.uuid4())


def S(client, h):
    return {s["code"]: s["id"] for s in client.get("/api/inv/master", headers=h).json()["skus"]}


def W(client, h):
    return {w["code"]: w["id"] for w in client.get("/api/inv/master", headers=h).json()["warehouses"]}


def setup_tt_skus(client, h):
    b = client.post("/api/batches", json={"kind": "sku_upsert", "rows": [
        {"code": "TT-CBS", "name": "Chili Bean Sauce 1kg×10", "category": "成品", "base_unit": "箱"},
        {"code": "TT-MLT", "name": "MLT Soup Base 10kg", "category": "成品", "base_unit": "箱"},
        {"code": "TT-SBP", "name": "Soy Bean Paste with Chili 10kg", "category": "成品", "base_unit": "箱"}]}, headers=h).json()
    client.post(f"/api/batches/{b['id']}/apply", json={"selected": [0, 1, 2]}, headers=h)
    sk, wh = S(client, h), W(client, h)
    for lot, exp, q in (("CBS-OLD", "2026-12-01", 4), ("CBS-NEW", "2027-03-01", 10)):
        client.post("/api/inv/receive", json={"idempotency_key": K(), "sku_id": sk["TT-CBS"], "warehouse_id": wh["WH597"], "qty": q, "lot_code": lot, "expiry": exp}, headers=h)
    client.post("/api/inv/receive", json={"idempotency_key": K(), "sku_id": sk["TT-MLT"], "warehouse_id": wh["WH597"], "qty": 2, "lot_code": "MLT1"}, headers=h)


def test_ORD01_system_po_via_chatgpt_map_and_ship_fefo(client, login):
    ops, qa = login("ops"), login("qa")
    setup_tt_skus(client, ops)
    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    po = {"po_no": "623030105", "po_date": "2026-10-30", "required_date": "2026-10-31", "category": "General", "site": "Chino Hills Store - 0KITCHEN DELI",
          "source_summary": "PO26103014 PDF 第 1 页", "lines": [
              {"item_no": "505078", "description": "CHILI BEAN SAUCE", "pack_size": "1kg/10.00", "unit": "case", "qty": 6, "price": 170, "sku_code": "TT-CBS"},
              {"item_no": "520687", "description": "MLT SOUP BASE", "pack_size": "10kg/1.00", "unit": "case", "qty": 6, "price": 170, "sku_code": "TT-MLT"},
              {"item_no": "793901", "description": "SOY BEAN PASTE WITH CHILI", "pack_size": "10kg/1.00", "unit": "case", "qty": 6, "price": 55}]}
    res = call(client, tok, "propose_order", po)["structuredContent"]
    assert res["summary"] == {"new": 2, "unmapped": 1} and "/u/" in res["upload_url"]
    assert any("库存不足" in " ".join(w["note"]) for w in res["warnings"])  # MLT only 2 in stock
    # PO original uploaded via the link → carried over to the order on confirm
    tokn = res["upload_url"].split("/u/")[1]
    assert client.post(f"/api/upload-link/{tokn}", files={"file": ("po.png", png(601), "image/png")}).status_code == 200
    bid = res["confirm_url"].split("batch=")[1]
    sk = S(client, ops)
    r = client.post(f"/api/batches/{bid}/apply", json={"selected": [0, 1, 2], "overrides": {"2": {"sku_id": sk["TT-SBP"]}}}, headers=ops).json()
    oid = r["result"]["order_id"]
    so = client.get(f"/api/orders/{oid}", headers=ops).json()
    assert so["po_no"] == "623030105" and so["unmapped"] == 0 and so["site"].startswith("Chino Hills") and so["overdue"] is True
    assert client.get(f"/api/evidence?target_type=so&target_id={oid}", headers=ops).json()[0]["purpose"] == "PO 原件"
    # mapping remembered: the same PO again → duplicate; a new PO with item 505078 auto-maps and flags price change
    dup = call(client, tok, "propose_order", {**po, "source_summary": "再发一次"})["structuredContent"]
    assert dup["summary"] == {"error": 3}
    nxt = call(client, tok, "propose_order", {"po_no": "623040001", "required_date": "2026-11-20", "source_summary": "下一张", "site": "Chino Hills",
                                              "lines": [{"item_no": "505078", "description": "CHILI BEAN SAUCE", "unit": "case", "qty": 2, "price": 175}]})["structuredContent"]
    assert nxt["summary"] == {"mapped": 1} and "上次 170" in " ".join(nxt["warnings"][0]["note"])
    # quarantine the oldest lot → FEFO skips it; an explicit pick of it is refused
    lots = {l["lot_code"]: l["id"] for l in client.get("/api/inv/master", headers=ops).json()["lots"]}
    client.post(f"/api/inv/lots/{lots['CBS-OLD']}/quarantine", json={"reason": "外箱受潮"}, headers=qa)
    line = so["lines"][0]
    bad = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": line["id"], "picks": [{"lot_id": lots["CBS-OLD"], "qty": 1}]}]}, headers=ops)
    assert bad.status_code == 409 and "隔离" in bad.text
    r = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": line["id"], "qty": 6}]}, headers=ops).json()
    picks = r["order"]["shipments"][0]["lines"][0]["picks"]
    assert [p["lot_code"] for p in picks] == ["CBS-NEW"] and r["order"]["status"] == "partial"
    # over-quantity blocked (T&T rejects), allowed with a reason (flagged)
    over = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": line["id"], "qty": 1}]}, headers=ops)
    assert over.status_code == 409 and "超出订量" in over.text
    # trace: the lot shows PO 623030105 as destination
    fwd = client.get(f"/api/inv/trace/{lots['CBS-NEW']}", headers=qa).json()["forward"]
    assert any(s["order_ref"] == "623030105" for n in fwd for s in n["shipments"])


def test_ORD02_central_kitchen_po_by_weight_with_boxes(client, login):
    ops = login("ops")
    sk, wh = S(client, ops), W(client, ops)
    tnt = next(c for c in client.get("/api/orders/customers", headers=ops).json() if c["code"] == "TNT")
    site = tnt["sites"][0]["id"]
    body = {"customer_id": tnt["id"], "site_id": site, "po_no": "CH-CK", "po_date": "2026-11-05", "required_date": "2026-11-10", "category": "中央厨房",
            "lines": [{"description": "Chicken Leg-Diced 切粒鸡丁", "unit": "lb", "qty": 300, "price": 3.60, "sku_id": sk["FG-CHK-DICE"]}]}
    oid = client.post("/api/orders", json=body, headers=ops).json()["id"]
    assert client.post("/api/orders", json=body, headers=ops).status_code == 409                         # same CH-CK, same date → duplicate
    assert client.post("/api/orders", json={**body, "po_date": "2026-11-12"}, headers=ops).status_code == 200  # CH-CK reused on another day is fine
    # weigh 6 boxes (~50 lb) and put them into stock; print labels
    packed = client.post("/api/orders/boxes", json={"sku_id": sk["FG-CHK-DICE"], "lot_code": "CK1106", "weights": [50.2, 49.8, 50.0, 50.4, 49.6, 50.1],
                                                    "post_stock": True, "idempotency_key": K()}, headers=ops).json()
    assert len(packed["boxes"]) == 6 and packed["total"] == 300.1 and packed["boxes"][0]["serial"].startswith("BX")
    assert client.get(f"/api/inv/scan/{packed['boxes'][0]['serial']}", headers=ops).json()["type"] == "box"
    so = client.get(f"/api/orders/{oid}", headers=ops).json()
    line = so["lines"][0]
    assert line["catch_weight"] and len(line["boxes_in_stock"]) == 6 and not line["short"]
    r = client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": line["id"], "box_ids": [b["id"] for b in packed["boxes"]]}]}, headers=ops).json()
    o = r["order"]
    assert o["status"] == "shipped" and o["lines"][0]["qty_shipped"] == 300.1 and o["lines"][0]["boxes_shipped"] == 6
    assert o["shipped_amount"] == round(300.1 * 3.60, 2)
    # boxes can't ship twice
    assert client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": line["id"], "box_ids": [packed["boxes"][0]["id"]]}]}, headers=ops).status_code == 409
    # deliver: POD required (or exception); 1 box rejected → discrepancy task
    sh = o["shipments"][0]["id"]
    assert client.post(f"/api/orders/shipments/{sh}/deliver", json={}, headers=ops).status_code == 409
    client.post("/api/evidence/upload", files={"file": ("pod.png", png(602), "image/png")}, data={"target_type": "shipment", "target_id": sh, "purpose": "签收单"}, headers=ops)
    d = client.post(f"/api/orders/shipments/{sh}/deliver", json={"rejected": [{"line_id": line["id"], "qty": 50.2, "reason": "一箱温度偏高"}]}, headers=ops).json()
    assert d["status"] == "delivered" and d["shipped_amount"] == round((300.1 - 50.2) * 3.60, 2)
    assert any("拒收" in t["title"] for t in client.get(f"/api/tasks?target_id={oid}", headers=ops).json())


def test_ORD03_boxes_label_only_needs_stock_and_metrics(client, login):
    ops = login("ops")
    sk = S(client, ops)
    r = client.post("/api/orders/boxes", json={"sku_id": sk["FG-CHK-DICE"], "lot_code": "NOSTOCK", "weights": [30], "post_stock": False, "idempotency_key": K()}, headers=ops)
    assert r.status_code == 409 and "称重入库" in r.text
    assert client.post("/api/orders/boxes", json={"sku_id": sk["TT-CBS"], "weights": [30], "idempotency_key": K()}, headers=ops).status_code == 409  # not catch-weight
    m = {x["key"]: x["value"] for x in client.get("/api/metrics?keys=orders_overdue,orders_due_soon,orders_short", headers=ops).json()}
    assert m["orders_overdue"] >= 1 and m["orders_short"] >= 1


def test_ORD04_chatgpt_reads_orders_and_proposes_new_date(client, login):
    ops = login("ops")
    _, tok = connect(client, "ops")
    lst = call(client, tok, "list_orders", {})["structuredContent"]
    assert lst["count"] >= 1
    d = call(client, tok, "get_order", {"order": "623030105"})["structuredContent"]
    assert d["po_no"] == "623030105" and "can" not in d
    res = call(client, tok, "propose_change", {"target_type": "so", "target_id": d["code"], "source_summary": "Simon 邮件：改到 11/3",
                                                "changes": [{"field": "交货日期", "new_value": "2026-11-03"}]})["structuredContent"]
    iid = res["confirm_url"].split("item=")[1]
    cs = client.get(f"/api/ingest/{iid}", headers=ops).json()["changesets"][0]
    client.post(f"/api/changesets/{cs['id']}/apply", json={"accept": ["required_date"]}, headers=ops)
    assert client.get(f"/api/orders/{d['id']}", headers=ops).json()["required_date"] == "2026-11-03"
    custs = call(client, tok, "list_customers", {"customer": "大统华"})["structuredContent"]["customers"]
    assert custs[0]["vendor_no"] == "10325" and any(i["item_no"] == "505078" for i in custs[0]["items"])
