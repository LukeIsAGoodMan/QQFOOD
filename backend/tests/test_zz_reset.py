"""清空演示数据（最后运行：会删除全部业务数据）。"""
import os


def test_reset_demo_data(client, login):
    admin, boss, ops = login("admin"), login("boss"), login("ops")
    assert client.post("/api/admin/reset-demo", json={"confirm": "清空"}, headers=admin).status_code == 400
    assert client.post("/api/admin/reset-demo", json={"confirm": "清空演示数据"}, headers=ops).status_code == 403
    r = client.post("/api/admin/reset-demo", json={"confirm": "清空演示数据"}, headers=admin).json()
    assert r["ok"] and r["backup"] and os.path.exists(r["backup"])
    assert r["deleted"]["skus"] > 0 and "ops" in r["deactivated_users"]
    assert client.get("/api/inv/stock", headers=admin).json()["rows"] == []
    assert client.get("/api/tasks", headers=boss).json() == []
    codes = {w["code"] for w in client.get("/api/inv/master", headers=admin).json()["warehouses"]}
    assert {"WH597", "TOR", "VAN"} <= codes and "EXT1" not in codes
    assert client.get("/api/pages", headers=boss).json()  # pages and settings kept
    assert client.get("/api/me", headers=ops).status_code == 401  # demo account deactivated
    assert client.get("/api/admin/status", headers=admin).json()["demo_cleared"]["backup"] == r["backup"]
