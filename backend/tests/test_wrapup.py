"""收尾三项：期初防重复入库、已传原件挂到单据、今日摘要。"""
import uuid

from app import brief
from app.db import SessionLocal
from conftest import png
from test_agent import call, connect, enable


def M(client, h):
    return client.get("/api/inv/master", headers=h).json()


def test_WRP01_count_and_inbound_warn_possible_duplicates(client, login):
    ops = login("ops")
    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    # 多伦多发来一批（在途，还没签收）
    res = call(client, tok, "propose_inbound", {"from_code": "TOR", "source_summary": "TOR 调货单", "lines": [{"sku_code": "RM-SPICE", "qty": 4, "lot_code": "DUP-L1"}]})["structuredContent"]
    bid = res["confirm_url"].split("batch=")[1]
    tr = client.post(f"/api/batches/{bid}/apply", json={"selected": [0]}, headers=ops).json()["result"]["transfer_code"]
    # 597 实盘时把同一个 Lot 点进去了 → 提示可能重复、默认不勾
    res = call(client, tok, "propose_stock_counts", {"source_summary": "597 实盘", "rows": [
        {"sku_code": "RM-SPICE", "warehouse_code": "WH597", "lot_code": "DUP-L1", "qty": 4},
        {"sku_code": "RM-SPICE", "warehouse_code": "WH597", "lot_code": "OTHER-L", "qty": 2}]})["structuredContent"]
    assert [d["row"] for d in res["possible_duplicates"]] == [1] and "note_for_ai_dup" in res
    rows = client.get(f"/api/batches/{res['confirm_url'].split('batch=')[1]}", headers=ops).json()["rows"]
    assert rows[0]["suspect"] and any(tr in m and "核销" in m for m in rows[0]["messages"])
    assert not rows[1].get("suspect")
    # 反过来：597 已有这个 Lot 的实物，再录调入 → 提示
    w = next(x["id"] for x in M(client, ops)["warehouses"] if x["code"] == "WH597")
    sid = next(s["id"] for s in M(client, ops)["skus"] if s["code"] == "RM-SPICE")
    client.post("/api/inv/receive", json={"idempotency_key": str(uuid.uuid4()), "sku_id": sid, "warehouse_id": w, "qty": 6, "lot_code": "DUP-L2"}, headers=ops)
    res = call(client, tok, "propose_inbound", {"from_code": "TOR", "source_summary": "又一张调货单", "lines": [{"sku_code": "RM-SPICE", "qty": 6, "lot_code": "DUP-L2"}]})["structuredContent"]
    assert res["possible_duplicates"] and "已有 Lot DUP-L2" in str(res["warnings"])


def test_WRP02_attach_task_proof_to_batch(client, login):
    ops = login("ops")
    _, tok = connect(client, "ops")
    t = call(client, tok, "create_task", {"title": "2602柜入库凭证（待补原件）", "kind": "task"})["structuredContent"]
    tid = t.get("id") or t["task"]["id"]
    url = call(client, tok, "request_upload_link", {"target_type": "task", "target_id": tid, "purpose": "装箱单"})["structuredContent"]["upload_url"]
    assert client.post(f"/api/upload-link/{url.split('/u/')[1]}", files={"file": ("pl-2602.png", png(2602), "image/png")}).status_code == 200
    # 资料库能按任务名搜到，并显示挂在哪里
    lib = client.get("/api/evidence/library?q=2602柜", headers=ops).json()
    assert lib and any("任务「2602柜入库凭证" in w for w in lib[0]["where"])
    # 单据建好了 → 把任务上的原件挂过去
    res = call(client, tok, "propose_stock_counts", {"source_summary": "2602 柜实盘", "rows": [{"sku_code": "RM-SPICE", "warehouse_code": "WH597", "qty": 9}]})["structuredContent"]
    code = client.get(f"/api/batches/{res['confirm_url'].split('batch=')[1]}", headers=ops).json()["code"]
    r = call(client, tok, "attach_existing_proof", {"from_type": "task", "from_id": tid, "to_type": "batch", "to_id": code})["structuredContent"]
    assert r["attached"] == 1 and code in r["to"]
    bid = res["confirm_url"].split("batch=")[1]
    ev = client.get(f"/api/evidence?target_type=batch&target_id={bid}", headers=ops).json()
    assert ev and ev[0]["purpose"] == "装箱单" and ev[0]["verify_status"] == "unverified"
    assert client.get(f"/api/evidence?target_type=task&target_id={tid}", headers=ops).json()  # 来源保留
    assert call(client, tok, "attach_existing_proof", {"from_type": "task", "from_id": tid, "to_type": "batch", "to_id": "BT-NOPE"}).get("isError")


def test_WRP03_daily_brief(client, login):
    ops, boss = login("ops"), login("boss")
    b = client.get("/api/brief", headers=ops).json()
    keys = {s["key"]: s for s in b["sections"]}
    assert {"orders_due", "orders_late", "batches", "low_stock", "expiring", "po_late", "missing_proof", "my_tasks"} <= set(keys)
    assert keys["batches"]["count"] >= 1 and all(i["link"].startswith("/ingest?batch=") for i in keys["batches"]["items"])
    assert "批待确认" in b["headline"]
    _, tok = connect(client, "ops")
    g = call(client, tok, "get_daily_brief", {})["structuredContent"]
    assert g["headline"] == b["headline"] and g["sections"][2]["items"][0]["link"].startswith("http")
    db = SessionLocal()
    try:
        n = brief.send_morning(db)
        assert n >= 2 and brief.send_morning(db) == 0  # 一天只发一次
    finally:
        db.close()
    notes = client.get("/api/notifications", headers=boss).json()
    notes = notes.get("items", notes) if isinstance(notes, dict) else notes
    assert any("今日摘要" in x["title"] for x in notes)
