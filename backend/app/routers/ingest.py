"""资料接入与更新中心 (PRD §9): upload → (recognize) → match → propose ChangeSet → confirm → apply with version check.

v0.1: recognition is not wired yet; users propose changes manually from the original document. The ChangeSet
pipeline (old/new values, target version pinning, conflict on concurrent edit, evidence linking) is the same one an
OCR/AI recognizer will feed later. Text inside files is data, never instructions.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import audit, current_user, get_or_404, require
from ..db import get_db
from ..models import (BusinessRecord, ChangeSet, EvidenceLink, EvidenceVersion, FieldDefinition, IngestItem, PurchaseOrder,
                      Task, User)
from .evidence import create_link, ingest_file, link_out
from .records import RecordPatch, apply_record_patch
from .tasks import TaskPatch, apply_task_patch

router = APIRouter(prefix="/api", tags=["ingest"])

TASK_FIELDS = {"title": "标题", "description": "说明", "due_at": "截止时间", "next_step": "下一步", "status": "状态"}
PO_FIELDS = {"expected_date": "预计到货", "note": "备注"}
SO_FIELDS = {"required_date": "要求交货日期", "note": "备注"}


def _so_model():
    from ..models import SalesOrder
    return SalesOrder


MODELS = {"record": BusinessRecord, "task": Task, "po": PurchaseOrder}


def _names(db):
    return {x.id: x.display_name for x in db.query(User).all()}


def _target(db: Session, ttype: str, tid: str):
    if ttype == "record":
        return get_or_404(db, BusinessRecord, tid, "目标记录不存在")
    if ttype == "task":
        return get_or_404(db, Task, tid, "目标任务不存在")
    if ttype == "po":
        return get_or_404(db, PurchaseOrder, tid, "目标采购单不存在")
    if ttype == "so":
        return get_or_404(db, _so_model(), tid, "目标订单不存在")
    raise HTTPException(400, "仅支持对记录、任务或采购单提出变更")


def _target_label(db, ttype, obj):
    return obj.title


def _current_value(obj, ttype, field):
    if ttype == "record" and field.startswith("data."):
        return (obj.data or {}).get(field[5:])
    v = getattr(obj, field, None)
    return v.isoformat() if isinstance(v, datetime) else v


def cs_out(db, c: ChangeSet, names):
    obj = db.get(MODELS.get(c.target_type) or _so_model(), c.target_id)
    return {"id": c.id, "target_type": c.target_type, "target_id": c.target_id,
            "target_title": obj.title if obj else "(已删除)", "target_version": c.target_version,
            "current_version": obj.version if obj else None,
            "stale": bool(obj and obj.version != c.target_version and c.status == "proposed"),
            "changes": c.changes, "status": c.status, "proposed_by": names.get(c.proposed_by),
            "proposed_via": c.proposed_via, "decided_by": names.get(c.decided_by), "decided_at": c.decided_at,
            "result": c.result, "created_at": c.created_at}


def item_out(db, it: IngestItem, u, names):
    ver_link = db.query(EvidenceLink).filter_by(target_type="ingest", target_id=it.id).first()
    cs = db.query(ChangeSet).filter_by(ingest_id=it.id).order_by(ChangeSet.created_at).all()
    created = datetime.fromisoformat(str(it.created_at)) if not isinstance(it.created_at, datetime) else it.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {"id": it.id, "mode": it.mode, "status": it.status, "source": it.source, "note": it.note,
            "context_type": it.context_type, "context_id": it.context_id, "owner": names.get(it.owner_id),
            "created_by": names.get(it.created_by), "created_at": it.created_at,
            "waiting_hours": round((datetime.now(timezone.utc) - created).total_seconds() / 3600, 1),
            "evidence": link_out(db, ver_link, u, names) if ver_link else None,
            "recognition": {"status": "not_configured", "msg": "识别服务未接入（v0.1）：请对照原资料手动提出变更"},
            "changesets": [cs_out(db, c, names) for c in cs]}


@router.post("/ingest")
async def ingest(file: UploadFile = File(...), mode: str = Form("suggest"), context_type: str | None = Form(None),
                 context_id: str | None = Form(None), note: str = Form(""), sensitivity: str = Form("normal"),
                 u: User = Depends(current_user), db: Session = Depends(get_db)):
    if mode not in ("suggest", "evidence_only"):
        raise HTTPException(400, "mode 需为 suggest 或 evidence_only")
    data = await file.read()
    ev, ver, dedup = ingest_file(db, u, data, file.filename or "upload", sensitivity=sensitivity, source="ingest")
    status = "evidence_only" if mode == "evidence_only" and context_id else "inbox"
    it = IngestItem(evidence_id=ev.id, mode=mode, status=status, context_type=context_type, context_id=context_id,
                    note=note, owner_id=u.id, created_by=u.id)
    db.add(it)
    db.flush()
    create_link(db, u, ver, "ingest", it.id, "来源资料")
    if context_type and context_id:
        # 就地上传携带上下文，但只作为候选；仅存证模式直接关联
        if mode == "evidence_only":
            create_link(db, u, ver, context_type, context_id, "来源资料")
    audit(db, u, "ingest.received", "ingest", it.id, mode=mode, dedup=dedup)
    db.commit()
    return {"id": it.id, "dedup": dedup, "status": it.status}


@router.get("/ingest")
def list_ingest(status: str | None = None, u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(IngestItem)
    if status:
        q = q.filter(IngestItem.status.in_(status.split(",")))
    names = _names(db)
    return [item_out(db, it, u, names) for it in q.order_by(IngestItem.created_at.desc()).limit(200)]


@router.get("/ingest/counts")
def ingest_counts(u: User = Depends(current_user), db: Session = Depends(get_db)):
    from sqlalchemy import func
    return {s: n for s, n in db.query(IngestItem.status, func.count()).group_by(IngestItem.status)}


@router.get("/ingest/{iid}")
def get_ingest(iid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return item_out(db, get_or_404(db, IngestItem, iid), u, _names(db))


class StatusIn(BaseModel):
    status: str
    note: str = ""


@router.post("/ingest/{iid}/status")
def set_status(iid: str, body: StatusIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    it = get_or_404(db, IngestItem, iid)
    if body.status not in ("evidence_only", "rejected", "inbox"):
        raise HTTPException(400, "只能手动设为 仅存证 / 已驳回 / 待整理")
    if body.status == "rejected" and not body.note.strip():
        raise HTTPException(400, "驳回需填写原因")
    it.status = body.status
    it.note = (it.note + "\n" if it.note else "") + body.note
    it.updated_at = datetime.now(timezone.utc)
    audit(db, u, "ingest.status", "ingest", it.id, status=body.status, note=body.note)
    db.commit()
    return {"ok": True}


class AssignIn(BaseModel):
    target_type: str
    target_id: str
    purpose: str = "来源资料"


@router.post("/ingest/{iid}/link")
def link_to_target(iid: str, body: AssignIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    it = get_or_404(db, IngestItem, iid)
    from ..models import Evidence
    ev = db.get(Evidence, it.evidence_id)
    ver = db.get(EvidenceVersion, ev.current_version_id)
    create_link(db, u, ver, body.target_type, body.target_id, body.purpose)
    it.context_type, it.context_id = body.target_type, body.target_id
    if it.status == "inbox" and it.mode == "evidence_only":
        it.status = "evidence_only"
    db.commit()
    return {"ok": True}


class ChangeIn(BaseModel):
    field: str  # record: title|status|data.<field_id> ; task: title|description|due_at|next_step|status
    new: str | float | bool | None
    reason: str = ""
    evidence_loc: str = ""  # 页码/区域，例如 "p1 右上 交期"


class ProposeIn(BaseModel):
    target_type: str
    target_id: str
    changes: list[ChangeIn]


@router.post("/ingest/{iid}/changesets")
def propose(iid: str, body: ProposeIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    it = get_or_404(db, IngestItem, iid)
    obj = _target(db, body.target_type, body.target_id)
    labels = {}
    if body.target_type == "record":
        labels = {f"data.{f.id}": f.label for f in db.query(FieldDefinition).filter_by(record_type_id=obj.record_type_id)}
        labels.update({"title": "标题", "status": "状态"})
    elif body.target_type == "po":
        labels = PO_FIELDS
    elif body.target_type == "so":
        labels = SO_FIELDS
    else:
        labels = TASK_FIELDS
    rows = []
    for ch in body.changes:
        if ch.field not in labels:
            raise HTTPException(400, f"字段 {ch.field} 未注册，不能映射（需人工映射）")
        rows.append({"field": ch.field, "label": labels[ch.field], "old": _current_value(obj, body.target_type, ch.field),
                     "new": ch.new, "reason": ch.reason, "evidence_loc": ch.evidence_loc, "decision": None})
    c = ChangeSet(ingest_id=it.id, target_type=body.target_type, target_id=obj.id, target_version=obj.version,
                  changes=rows, proposed_by=u.id)
    db.add(c)
    it.status = "pending_confirm"
    it.updated_at = datetime.now(timezone.utc)
    db.flush()
    audit(db, u, "changeset.proposed", "ingest", it.id, changeset=c.id, target=obj.id, fields=[r["field"] for r in rows])
    db.commit()
    return cs_out(db, c, _names(db))


class ApplyIn(BaseModel):
    accept: list[str]  # field keys to apply; others rejected
    edits: dict = {}   # field -> edited new value


def _refresh_item(db: Session, it: IngestItem):
    cs = db.query(ChangeSet).filter_by(ingest_id=it.id).all()
    if any(c.status == "conflict" for c in cs):
        it.status = "conflict"
    elif any(c.status == "proposed" for c in cs):
        it.status = "pending_confirm"
    elif any(c.status in ("applied", "partial") for c in cs):
        it.status = "applied"
    elif cs:
        it.status = "rejected"
    it.updated_at = datetime.now(timezone.utc)


@router.post("/changesets/{cid}/apply")
def apply(cid: str, body: ApplyIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "ingest.apply")
    c = get_or_404(db, ChangeSet, cid)
    if c.status not in ("proposed",):
        raise HTTPException(409, f"变更集状态为 {c.status}，不能再次应用")
    it = db.get(IngestItem, c.ingest_id)
    obj = _target(db, c.target_type, c.target_id)
    if obj.version != c.target_version:
        c.status = "conflict"
        c.result = f"审阅期间目标已被修改（v{c.target_version} → v{obj.version}），未覆盖"
        _refresh_item(db, it)
        audit(db, u, "changeset.conflict", "ingest", it.id, changeset=c.id)
        db.commit()
        raise HTTPException(409, c.result)
    rows = [dict(r) for r in c.changes]
    accepted = []
    for r in rows:
        if r["field"] in body.accept:
            if r["field"] in body.edits:
                r["new"] = body.edits[r["field"]]
                r["edited"] = True
            r["decision"] = "accepted"
            accepted.append(r)
        else:
            r["decision"] = "rejected"
    via = f"ingest:{it.id}"
    if accepted:
        if c.target_type == "record":
            data = {r["field"][5:]: r["new"] for r in accepted if r["field"].startswith("data.")}
            patch = RecordPatch(version=obj.version, data=data or None,
                                title=next((r["new"] for r in accepted if r["field"] == "title"), None),
                                status=next((r["new"] for r in accepted if r["field"] == "status"), None))
            apply_record_patch(db, u, obj, patch, via=via)  # raises on evidence-rule / permission failure → nothing applied
        elif c.target_type == "so":
            from .orders import OrderPatch, apply_order_patch
            apply_order_patch(db, u, obj, OrderPatch(version=obj.version, **{r["field"]: r["new"] for r in accepted}), via=via)
        elif c.target_type == "po":
            from .purchasing import POPatch, apply_po_patch
            apply_po_patch(db, u, obj, POPatch(version=obj.version, **{r["field"]: r["new"] for r in accepted}), via=via)
        else:
            kw = {r["field"]: r["new"] for r in accepted}
            if "due_at" in kw and kw["due_at"]:
                kw["due_at"] = datetime.fromisoformat(str(kw["due_at"]))
            apply_task_patch(db, u, obj, TaskPatch(version=obj.version, **kw), via=via)
        from ..models import Evidence
        ev = db.get(Evidence, it.evidence_id)
        create_link(db, u, db.get(EvidenceVersion, ev.current_version_id), c.target_type, c.target_id, "变更依据")
    c.changes = rows
    c.status = "applied" if len(accepted) == len(rows) else ("partial" if accepted else "rejected")
    c.decided_by, c.decided_at = u.id, datetime.now(timezone.utc)
    c.result = f"已应用 {len(accepted)} 项，驳回 {len(rows) - len(accepted)} 项。仅写入获批字段，不代表收货核验/付款核销/质量放行。"
    _refresh_item(db, it)
    audit(db, u, "changeset.applied", "ingest", it.id, changeset=c.id, accepted=[r["field"] for r in accepted])
    db.commit()
    return cs_out(db, c, _names(db))


@router.post("/changesets/{cid}/reject")
def reject(cid: str, body: StatusIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c = get_or_404(db, ChangeSet, cid)
    if c.status not in ("proposed", "conflict"):
        raise HTTPException(409, "已处理")
    c.status, c.result = "rejected", body.note
    c.decided_by, c.decided_at = u.id, datetime.now(timezone.utc)
    _refresh_item(db, db.get(IngestItem, c.ingest_id))
    audit(db, u, "changeset.rejected", "ingest", c.ingest_id, changeset=c.id, note=body.note)
    db.commit()
    return {"ok": True}


@router.post("/changesets/{cid}/rebase")
def rebase(cid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """冲突处理：基于目标最新版本重新取原值，回到待确认，由人重新核对。"""
    c = get_or_404(db, ChangeSet, cid)
    if c.status != "conflict":
        raise HTTPException(409, "只有冲突状态可以重新核对")
    obj = _target(db, c.target_type, c.target_id)
    c.changes = [{**r, "old": _current_value(obj, c.target_type, r["field"]), "decision": None} for r in c.changes]
    c.target_version, c.status, c.result = obj.version, "proposed", ""
    _refresh_item(db, db.get(IngestItem, c.ingest_id))
    db.commit()
    return cs_out(db, c, _names(db))
