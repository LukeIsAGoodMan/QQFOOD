"""库存分类：干货 / 冻货 / 原料肉 / 原料菜 / 生产用品 / 公司用品 / 生产机器（+ 未分类 兜底）。"""
from app.categories import SKU_CATEGORIES, guess, normalize
from app.db import SessionLocal
from app.models import AppSetting, Sku
from app.seed import upgrade_categories
from test_agent import call, connect, enable
from test_purchasing import _new_po


def master(client, h):
    return client.get("/api/inv/master", headers=h).json()


def test_CAT01_guess_and_normalize():
    assert SKU_CATEGORIES == ["干货", "冻货", "原料肉", "原料菜", "生产用品", "公司用品", "生产机器"]
    assert guess("示例 鸡腿肉（原料）", "原料") == "原料肉"
    assert guess("大白菜", "原料") == "原料菜"
    assert guess("香料包", "原料") == "干货"
    assert guess("猪肉白菜水饺 1kg", "成品") == "冻货"
    assert guess("MLT Soup Base 10kg", "成品") == "干货"
    assert guess("切粒咕咾肉", "成品") == "未分类"      # 切好的肉冻不冻说不准 → 让人选
    assert guess("包装托盘", "包材") == "生产用品"
    assert guess("打印纸", "耗材") == "公司用品"
    assert normalize("frozen") == "冻货" and normalize("设备") == "生产机器" and normalize("") == "未分类"
    assert normalize("乱填") is None


def test_CAT02_migration_moves_old_categories(client, login):
    db = SessionLocal()
    try:
        db.add(Sku(code="OLD-VEG", name="青葱", category="原料", base_unit="磅"))
        db.add(Sku(code="OLD-FG", name="切粒鸡丁 B", category="成品", base_unit="磅"))
        mk = db.get(AppSetting, "migration:categories_v1")
        db.delete(mk)
        db.commit()
    finally:
        db.close()
    upgrade_categories()
    m = {s["code"]: s["category"] for s in master(client, login("ops"))["skus"]}
    assert m["OLD-VEG"] == "原料菜" and m["OLD-FG"] == "未分类"
    assert all(c in SKU_CATEGORIES + ["未分类"] for c in m.values())
    upgrade_categories()  # 只跑一次，不会覆盖人改过的


def test_CAT03_set_category_in_app(client, login):
    ops, staff = login("ops"), login("staff1")
    sid = next(s["id"] for s in master(client, ops)["skus"] if s["code"] == "OLD-FG")
    assert client.patch(f"/api/inv/skus/{sid}", json={"category": "冻货"}, headers=staff).status_code == 403
    assert client.patch(f"/api/inv/skus/{sid}", json={"category": "乱填"}, headers=ops).status_code == 400
    assert client.patch(f"/api/inv/skus/{sid}", json={"category": "冻货"}, headers=ops).json()["category"] == "冻货"
    assert client.patch(f"/api/inv/skus/{sid}", json={"name": "示例 香料包"}, headers=ops).status_code == 409  # 品名唯一
    rows = client.get("/api/inv/stock", headers=ops).json()["rows"]
    assert rows and all("sku_category" in r for r in rows)
    # 新建商品：不给分类 = 未分类；旧写法照样认
    r = client.post("/api/inv/skus", json={"name": "一次性手套"}, headers=ops).json()
    assert next(s for s in master(client, ops)["skus"] if s["id"] == r["id"])["category"] == "未分类"
    assert client.post("/api/inv/skus", json={"name": "切肉机", "category": "设备"}, headers=ops).status_code == 200
    assert next(s for s in master(client, ops)["skus"] if s["name"] == "切肉机")["category"] == "生产机器"


def test_CAT04_chatgpt_sets_categories(client, login):
    ops = login("ops")
    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    res = call(client, tok, "propose_skus", {"source_summary": "分类整理", "rows": [
        {"name": "一次性手套", "category": "生产用品"},      # 只改分类
        {"name": "牛油火锅底料", "category": "干货", "base_unit": "箱"},
        {"name": "冻虾仁", "base_unit": "箱"},               # 不给 → 未分类，提示
        {"name": "x2", "category": "乱填"}]})["structuredContent"]
    bid = res["confirm_url"].split("batch=")[1]
    b = client.get(f"/api/batches/{bid}", headers=ops).json()
    by = {r["key"]: r for r in b["rows"]}
    assert by["一次性手套"]["action"] == "update" and by["一次性手套"]["changes"]["category"] == ["未分类", "生产用品"]
    assert by["x2"]["action"] == "error"
    assert any("没分类" in m for m in by["冻虾仁"]["messages"])
    client.post(f"/api/batches/{bid}/apply", json={"selected": [r["index"] for r in b["rows"] if r["action"] in ("new", "update")]}, headers=ops)
    m = {s["name"]: s["category"] for s in master(client, ops)["skus"]}
    assert m["一次性手套"] == "生产用品" and m["牛油火锅底料"] == "干货" and m["冻虾仁"] == "未分类"


def test_CAT05_food_needs_lot_supplies_do_not(client, login):
    buyer, staff = login("buyer"), login("staff1")
    pid = _new_po(client, buyer, qty=2, price=5, sku_code="RM-BEEF")
    client.post(f"/api/purchasing/pos/{pid}/submit", headers=buyer)
    client.post(f"/api/purchasing/pos/{pid}/order", headers=buyer)
    line = client.get(f"/api/purchasing/pos/{pid}", headers=buyer).json()["lines"][0]["id"]
    r = client.post(f"/api/purchasing/pos/{pid}/receipts", json={"lines": [{"po_line_id": line, "qty": 2}]}, headers=staff)
    assert r.status_code == 400 and "原料肉" in r.text
    pid = _new_po(client, buyer, qty=2, price=5, sku_code="PK-TRAY")
    client.post(f"/api/purchasing/pos/{pid}/submit", headers=buyer)
    client.post(f"/api/purchasing/pos/{pid}/order", headers=buyer)
    line = client.get(f"/api/purchasing/pos/{pid}", headers=buyer).json()["lines"][0]["id"]
    assert client.post(f"/api/purchasing/pos/{pid}/receipts", json={"lines": [{"po_line_id": line, "qty": 2}]}, headers=staff).status_code == 200
