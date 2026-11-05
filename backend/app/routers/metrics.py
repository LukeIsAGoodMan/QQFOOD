"""Boss dashboard metrics (PRD 附录B 指标口径). Each metric returns value, status, 口径, freshness and a drill link.
Unknown never shows as normal."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core import current_user
from ..db import get_db
from ..models import (BusinessRecord, ChecklistRun, EvidenceLink, IngestItem, Lot, Page, PageVersion, RecordType,
                      Site, Task, Transfer, User)
from .inventory import stock

router = APIRouter(prefix="/api", tags=["metrics"])

OPEN = ["todo", "doing", "blocked", "review"]

DEFS = {
    "overdue_tasks": ("逾期任务", "未完成/未取消且截止时间早于当前时间的任务与异常数", "/tasks?f=overdue"),
    "blocked_tasks": ("受阻任务", "状态为「受阻」的任务数", "/tasks?f=blocked"),
    "review_tasks": ("待核验任务", "现场已报告完成、等待核验人确认的任务数（含缺证据）", "/tasks?f=review"),
    "open_exceptions": ("未关闭异常", "类型为异常且未完成/未取消的数量（检查不合格、调拨差异等）", "/tasks?f=exceptions"),
    "quarantined_lots": ("隔离/待放行批次", "质量状态为隔离或待放行的批次数", "/quality"),
    "below_safety": ("低于安全库存", "可用量（实物−隔离−预留）低于该仓安全库存的 SKU×仓库 数", "/inventory"),
    "unverified_evidence": ("待核验证据", "有效关联中核验状态为「未核验」的数量", "/evidence-review"),
    "pending_ingest": ("资料待处理", "资料中心中待整理/待确认/冲突的条目，加上待确认的批量导入", "/ingest"),
    "in_transit": ("在途调拨", "已发运未完全签收的调拨单数", "/inventory"),
    "po_pending_approval": ("待审批采购", "金额超过审批额度、等待老板签批的采购单数（含补录采购的事后审批）", "/purchasing"),
    "po_overdue": ("到货逾期", "已下单或部分到货、预计到货日早于今天（场地时区）的采购单数", "/purchasing"),
    "receipts_pending": ("收货待入账", "现场已提交、尚未由仓管签名入账的收货单数", "/purchasing"),
    "missing_checklists": ("今日应填未提交", "已发布页面中设为每日必填、今天（场地时区）尚无提交的检查表数", "/tasks"),
}


def _site_today_start(db: Session) -> datetime:
    site = db.query(Site).first()
    tz = ZoneInfo(site.tz if site else "America/Los_Angeles")
    local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def compute(db: Session, u: User, key: str) -> dict:
    now = datetime.now(timezone.utc)
    if key.startswith("records:"):
        _, rt_id, *rest = key.split(":")
        rt = db.get(RecordType, rt_id)
        if not rt:
            return {"key": key, "label": "记录数", "value": None, "status": "unknown", "definition": "记录类型不存在",
                    "computed_at": now, "drill": ""}
        q = db.query(BusinessRecord).filter_by(record_type_id=rt_id)
        statuses = rest[0].split("|") if rest and rest[0] else []
        if statuses:
            q = q.filter(BusinessRecord.status.in_(statuses))
        n = q.count()
        return {"key": key, "label": f"{rt.name}{'（' + '/'.join(statuses) + '）' if statuses else ''}", "value": n,
                "status": "ok", "definition": f"记录类型「{rt.name}」中状态为 {'/'.join(statuses) or '全部'} 的记录数",
                "computed_at": now, "drill": ""}
    label, definition, drill = DEFS.get(key, (key, "未定义的指标", ""))
    value, status = None, "unknown"
    if key == "overdue_tasks":
        value = db.query(Task).filter(Task.status.in_(OPEN), Task.due_at.isnot(None), Task.due_at < now).count()
    elif key == "blocked_tasks":
        value = db.query(Task).filter_by(status="blocked").count()
    elif key == "review_tasks":
        value = db.query(Task).filter_by(status="review").count()
    elif key == "open_exceptions":
        value = db.query(Task).filter(Task.kind == "exception", Task.status.in_(OPEN)).count()
    elif key == "quarantined_lots":
        value = db.query(Lot).filter(Lot.quality_status.in_(["quarantine", "pending"])).count()
    elif key == "below_safety":
        rows = stock(None, u, db)["rows"]
        value = sum(1 for r in rows if r["below_safety"])
    elif key == "unverified_evidence":
        value = db.query(EvidenceLink).filter_by(active=True, verify_status="unverified").filter(
            EvidenceLink.target_type.notin_(["ingest"])).count()
    elif key == "pending_ingest":
        from ..models import BatchProposal
        value = db.query(IngestItem).filter(IngestItem.status.in_(["inbox", "pending_confirm", "conflict"])).count() \
            + db.query(BatchProposal).filter_by(status="proposed").count()
    elif key == "in_transit":
        value = db.query(Transfer).filter(Transfer.status.in_(["shipped", "partial"])).count()
    elif key in ("po_pending_approval", "po_overdue", "receipts_pending"):
        from ..models import PurchaseOrder, Receipt
        if key == "po_pending_approval":
            from .purchasing import _awaiting_approval
            value = sum(1 for p in db.query(PurchaseOrder).filter(PurchaseOrder.status.in_(["pending_approval", "received", "closed"]))
                        if _awaiting_approval(p))
        elif key == "po_overdue":
            today = datetime.now(ZoneInfo((db.query(Site).first() or Site(tz="America/Los_Angeles")).tz)).date().isoformat()
            value = db.query(PurchaseOrder).filter(PurchaseOrder.status.in_(["ordered", "partial"]), PurchaseOrder.expected_date.isnot(None),
                                                   PurchaseOrder.expected_date < today).count()
        else:
            value = db.query(Receipt).filter_by(status="submitted").count()
    elif key == "missing_checklists":
        start = _site_today_start(db)
        required, missing = 0, []
        for p in db.query(Page).filter_by(status="active"):
            v = db.get(PageVersion, p.published_version_id) if p.published_version_id else None
            for m in (v.layout["modules"] if v else []):
                if m["type"] == "checklist" and m.get("config", {}).get("daily_required") and not m.get("hidden"):
                    required += 1
                    if not db.query(ChecklistRun).filter(ChecklistRun.module_id == m["id"],
                                                         ChecklistRun.submitted_at >= start).count():
                        missing.append(f"{p.name}/{m.get('title') or '检查表'}")
        value = len(missing)
        definition += ("；未提交：" + "、".join(missing)) if missing else ""
        if required == 0:
            value, status = None, "unknown"
            definition = "没有设为每日必填的检查表，无法判断"
            return {"key": key, "label": label, "value": value, "status": status, "definition": definition,
                    "computed_at": now, "drill": drill}
    if value is not None:
        status = "ok" if value == 0 else "warn"
    return {"key": key, "label": label, "value": value, "status": status, "definition": definition,
            "computed_at": now, "drill": drill}


@router.get("/metrics")
def metrics(keys: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return [compute(db, u, k) for k in keys.split(",") if k]


@router.get("/metrics/catalog")
def catalog(u: User = Depends(current_user)):
    return [{"key": k, "label": v[0], "definition": v[1]} for k, v in DEFS.items()]


@router.get("/my-shift")
def my_shift(u: User = Depends(current_user), db: Session = Depends(get_db)):
    """当班视图：我的未完成任务 + 我可见页面里每日必填的检查表及今日是否已提交。"""
    from .tasks import task_out
    names = {x.id: x.display_name for x in db.query(User).all()}
    tasks = db.query(Task).filter(Task.owner_id == u.id, Task.status.in_(OPEN)).order_by(Task.due_at.is_(None), Task.due_at).all()
    start = _site_today_start(db)
    checks = []
    for p in db.query(Page).filter_by(status="active").order_by(Page.sort_order):
        from ..core import is_superuser
        if p.visible_roles and u.role not in p.visible_roles and not is_superuser(u):
            continue
        v = db.get(PageVersion, p.published_version_id) if p.published_version_id else None
        for m in (v.layout["modules"] if v else []):
            if m["type"] == "checklist" and m.get("config", {}).get("daily_required") and not m.get("hidden"):
                last = db.query(ChecklistRun).filter(ChecklistRun.module_id == m["id"]).order_by(ChecklistRun.submitted_at.desc()).first()
                done_today = bool(last and (last.submitted_at.replace(tzinfo=timezone.utc) if last.submitted_at.tzinfo is None else last.submitted_at) >= start)
                checks.append({"page_id": p.id, "page": p.name, "module_id": m["id"], "title": m.get("title") or "检查表",
                               "done_today": done_today, "last_at": last.submitted_at if last else None,
                               "last_passed": last.passed if last else None})
    return {"tasks": [task_out(db, t, names) for t in tasks], "checklists": checks}
