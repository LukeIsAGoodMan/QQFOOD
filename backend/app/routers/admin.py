"""系统维护：清空演示数据（导入真实数据前使用）。清空前自动在数据盘上做一份数据库备份。"""
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..core import audit, current_user
from ..db import DATA_DIR, DB_URL, UPLOAD_DIR, engine, get_db
from ..models import AccountEntry, AppSetting, User, Warehouse

router = APIRouter(prefix="/api/admin", tags=["admin"])

CONFIRM = "清空演示数据"
# delete order respects foreign keys
BUSINESS_TABLES = [
    "inventory_txns", "inventory_postings", "reservations", "transfer_lines", "transfers", "production_batches",
    "quality_events", "po_lines", "receipts", "purchase_orders", "quotes", "requisition_versions", "requisitions",
    "safety_stock", "lots", "skus", "change_sets", "ingest_items", "evidence_links", "evidence_versions", "evidence",
    "batch_proposals", "record_history", "comments", "checklist_runs", "tasks", "records", "notifications",
]
DEMO_USERS = ["ops", "buyer", "wh", "prod", "qa", "hrfin", "staff1", "staff2", "pageadmin"]


def backup_db() -> str | None:
    if not DB_URL.startswith("sqlite"):
        return None
    d = DATA_DIR / "backups"
    d.mkdir(exist_ok=True)
    path = d / f"qqfood-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(DB_URL.replace("sqlite:///", ""))
    dst = sqlite3.connect(path)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return str(path)


class ResetIn(BaseModel):
    confirm: str
    deactivate_demo_users: bool = True


@router.post("/reset-demo")
def reset_demo(body: ResetIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if u.role not in ("boss", "sys_admin"):
        raise HTTPException(403, "只有老板或系统管理员可以清空数据")
    if body.confirm.strip() != CONFIRM:
        raise HTTPException(400, f"请输入「{CONFIRM}」以确认")
    backup = backup_db()
    db.close()
    counts = {}
    with engine.begin() as conn:
        for t in BUSINESS_TABLES:
            counts[t] = conn.execute(text(f"DELETE FROM {t}")).rowcount
    db2 = Session(engine)
    try:
        for a in db2.query(AccountEntry).filter(AccountEntry.service.like("示例%")):
            db2.delete(a)
        for w in db2.query(Warehouse).filter(Warehouse.name.like("%示例%")):
            db2.delete(w)
        deactivated = []
        if body.deactivate_demo_users:
            for x in db2.query(User).filter(User.username.in_(DEMO_USERS), User.id != u.id):
                x.active = False
                deactivated.append(x.username)
        db2.merge(AppSetting(key="demo_cleared", value={"at": datetime.now().isoformat(), "by": u.id, "backup": backup}))
        audit(db2, db2.get(User, u.id), "admin.reset_demo", "system", "demo", backup=backup, deleted=counts, deactivated=deactivated)
        db2.commit()
    finally:
        db2.close()
    removed = 0
    for f in UPLOAD_DIR.rglob("*"):
        if f.is_file():
            f.unlink()
            removed += 1
    return {"ok": True, "backup": backup, "deleted": {k: v for k, v in counts.items() if v}, "files_removed": removed,
            "deactivated_users": deactivated}


@router.get("/status")
def status(u: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(AppSetting, "demo_cleared")
    return {"demo_cleared": s.value if s else None, "can_reset": u.role in ("boss", "sys_admin"), "confirm_phrase": CONFIRM}
