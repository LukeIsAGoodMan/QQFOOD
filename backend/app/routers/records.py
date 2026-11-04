"""Record types, field definitions, generic business records, comments, history (PRD §3.5, §8)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import audit, can, current_user, get_or_404, notify, require
from ..db import get_db
from ..models import BusinessRecord, Comment, EvidenceLink, FieldDefinition, RecordHistory, RecordType, Task, User

router = APIRouter(prefix="/api", tags=["records"])

FTYPES = {"text", "longtext", "number", "money", "date", "select", "person", "checkbox"}


def _field(f: FieldDefinition):
    return {"id": f.id, "label": f.label, "ftype": f.ftype, "options": f.options, "required": f.required,
            "core": f.core, "active": f.active, "sort_order": f.sort_order}


def _rt(db: Session, r: RecordType, with_counts=False):
    fs = db.query(FieldDefinition).filter_by(record_type_id=r.id).order_by(FieldDefinition.sort_order).all()
    d = {"id": r.id, "name": r.name, "description": r.description, "statuses": r.statuses, "core": r.core,
         "archived": r.archived, "evidence_rules": r.evidence_rules, "fields": [_field(f) for f in fs]}
    if with_counts:
        d["record_count"] = db.query(BusinessRecord).filter_by(record_type_id=r.id).count()
    return d


# ---------- record types ----------
@router.get("/record-types")
def list_types(u: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_rt(db, r, True) for r in db.query(RecordType).order_by(RecordType.name).all()]


@router.get("/record-types/{rid}")
def get_type(rid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return _rt(db, get_or_404(db, RecordType, rid), True)


class TypeIn(BaseModel):
    name: str
    description: str = ""
    statuses: list[str] = ["草稿", "进行中", "待核验", "已完成"]
    evidence_rules: dict = {}
    archived: bool = False


@router.post("/record-types")
def create_type(body: TypeIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "schema.edit")
    if not body.statuses:
        raise HTTPException(400, "至少需要一个状态")
    r = RecordType(name=body.name, description=body.description, statuses=body.statuses,
                   evidence_rules=body.evidence_rules)
    db.add(r)
    audit(db, u, "schema.type_created", "record_type", r.id, name=body.name)
    db.commit()
    return _rt(db, r)


@router.patch("/record-types/{rid}")
def update_type(rid: str, body: TypeIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "schema.edit")
    r = get_or_404(db, RecordType, rid)
    in_use = {s for (s,) in db.query(BusinessRecord.status).filter_by(record_type_id=rid).distinct()}
    removed = in_use - set(body.statuses)
    if removed:
        raise HTTPException(409, f"状态 {', '.join(removed)} 仍有记录在使用，不能删除")
    unknown_rule = set(body.evidence_rules) - set(body.statuses)
    if unknown_rule:
        raise HTTPException(400, f"证据规则引用了不存在的状态：{', '.join(unknown_rule)}")
    r.name, r.description, r.statuses, r.evidence_rules, r.archived = (
        body.name, body.description, body.statuses, body.evidence_rules, body.archived)
    audit(db, u, "schema.type_updated", "record_type", r.id, name=body.name)
    db.commit()
    return _rt(db, r)


class FieldIn(BaseModel):
    label: str
    ftype: str = "text"
    options: list[str] = []
    required: bool = False
    active: bool = True
    sort_order: int = 100
    allow_existing_empty: bool = False  # 设为必填时：旧记录保留空值并标记待补


def _records_with_value(db: Session, f: FieldDefinition) -> int:
    n = 0
    for (data,) in db.query(BusinessRecord.data).filter_by(record_type_id=f.record_type_id):
        v = (data or {}).get(f.id)
        if v not in (None, "", []):
            n += 1
    return n


@router.post("/record-types/{rid}/fields")
def add_field(rid: str, body: FieldIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "schema.edit")
    rt = get_or_404(db, RecordType, rid)
    if body.ftype not in FTYPES:
        raise HTTPException(400, "不支持的字段类型")
    existing = db.query(BusinessRecord).filter_by(record_type_id=rid).count()
    if body.required and existing and not body.allow_existing_empty:
        raise HTTPException(409, {"msg": f"已有 {existing} 条记录。新增必填字段需说明旧记录处理办法",
                                  "need": "allow_existing_empty"})
    f = FieldDefinition(record_type_id=rt.id, label=body.label, ftype=body.ftype, options=body.options,
                        required=body.required, sort_order=body.sort_order)
    db.add(f)
    audit(db, u, "schema.field_added", "field", f.id, record_type=rid, label=body.label)
    db.commit()
    return _field(f)


@router.patch("/fields/{fid}")
def update_field(fid: str, body: FieldIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "schema.edit")
    f = get_or_404(db, FieldDefinition, fid)
    if body.ftype != f.ftype:
        if f.core:
            raise HTTPException(409, "核心字段不能修改类型")
        n = _records_with_value(db, f)
        if n:
            raise HTTPException(409, f"已有 {n} 条记录填写了此字段，不能直接修改类型；请新建字段并迁移")
    if body.required and not f.required:
        missing = db.query(BusinessRecord).filter_by(record_type_id=f.record_type_id).count() - _records_with_value(db, f)
        if missing and not body.allow_existing_empty:
            raise HTTPException(409, {"msg": f"{missing} 条旧记录此字段为空。设为必填需说明旧记录处理办法",
                                      "need": "allow_existing_empty"})
    if f.core and not body.active:
        raise HTTPException(409, "核心字段不能停用")
    old = _field(f)
    f.label, f.ftype, f.options, f.required, f.active, f.sort_order = (
        body.label, body.ftype, body.options, body.required, body.active, body.sort_order)
    audit(db, u, "schema.field_updated", "field", f.id, before=old, after=_field(f))
    db.commit()
    return _field(f)


@router.delete("/fields/{fid}")
def delete_field(fid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "schema.edit")
    f = get_or_404(db, FieldDefinition, fid)
    if f.core:
        raise HTTPException(409, "核心字段不能删除")
    n = _records_with_value(db, f)
    if n:
        raise HTTPException(409, f"{n} 条记录有此字段的历史值，不能删除；可改为停用以保留历史")
    audit(db, u, "schema.field_deleted", "field", f.id, label=f.label)
    db.delete(f)
    db.commit()
    return {"ok": True}


# ---------- records ----------
def _rec(r: BusinessRecord, names: dict):
    return {"id": r.id, "record_type_id": r.record_type_id, "title": r.title, "status": r.status,
            "owner_id": r.owner_id, "owner": names.get(r.owner_id), "data": r.data, "version": r.version,
            "created_at": r.created_at, "updated_at": r.updated_at}


def _names(db):
    return {x.id: x.display_name for x in db.query(User).all()}


@router.get("/records")
def list_records(type: str, status: str | None = None, q: str | None = None, owner: str | None = None,
                 limit: int = 200, u: User = Depends(current_user), db: Session = Depends(get_db)):
    qry = db.query(BusinessRecord).filter_by(record_type_id=type)
    if status:
        qry = qry.filter(BusinessRecord.status.in_(status.split(",")))
    if q:
        qry = qry.filter(BusinessRecord.title.ilike(f"%{q}%"))
    if owner == "me":
        qry = qry.filter_by(owner_id=u.id)
    names = _names(db)
    rows = qry.order_by(BusinessRecord.updated_at.desc()).limit(min(limit, 1000)).all()
    ev_counts = {}
    for (tid,) in db.query(EvidenceLink.target_id).filter(EvidenceLink.target_type == "record", EvidenceLink.active,
                                                          EvidenceLink.target_id.in_([r.id for r in rows])):
        ev_counts[tid] = ev_counts.get(tid, 0) + 1
    return [{**_rec(r, names), "evidence_count": ev_counts.get(r.id, 0)} for r in rows]


def _validate_data(db: Session, rt: RecordType, data: dict, partial: bool) -> dict:
    fields = {f.id: f for f in db.query(FieldDefinition).filter_by(record_type_id=rt.id)}
    clean = {}
    for k, v in (data or {}).items():
        f = fields.get(k)
        if not f:
            raise HTTPException(400, f"未知字段 {k}")
        if not f.active:
            continue  # 停用字段保留历史值，不再接受写入
        if v in ("", None):
            clean[k] = None
            continue
        if f.ftype in ("number", "money"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"「{f.label}」需要数字")
        if f.ftype == "select" and f.options and v not in f.options:
            raise HTTPException(400, f"「{f.label}」选项无效")
        clean[k] = v
    return clean


def _missing_required(db: Session, rt: RecordType, data: dict) -> list[str]:
    return [f.label for f in db.query(FieldDefinition).filter_by(record_type_id=rt.id, required=True, active=True)
            if data.get(f.id) in (None, "", [])]


def check_evidence_rule(db: Session, rt: RecordType, record_id: str, status: str):
    rule = (rt.evidence_rules or {}).get(status)
    if not rule:
        return
    links = db.query(EvidenceLink).filter_by(target_type="record", target_id=record_id, active=True).all()
    missing = []
    for purpose in rule.get("purposes", []):
        ok = [l for l in links if l.purpose == purpose and l.verify_status != "rejected"
              and (not rule.get("need_approved") or l.verify_status == "approved")]
        if not ok:
            missing.append(purpose)
    if missing:
        need = "已核验的" if rule.get("need_approved") else ""
        raise HTTPException(409, f"进入「{status}」需要{need}证据：{'、'.join(missing)}")


class RecordIn(BaseModel):
    record_type_id: str
    title: str
    status: str | None = None
    owner_id: str | None = None
    data: dict = {}


@router.post("/records")
def create_record(body: RecordIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    rt = get_or_404(db, RecordType, body.record_type_id)
    if rt.archived:
        raise HTTPException(409, "记录类型已归档")
    data = _validate_data(db, rt, body.data, False)
    missing = _missing_required(db, rt, data)
    if missing:
        raise HTTPException(400, f"必填：{'、'.join(missing)}")
    status = body.status or rt.statuses[0]
    if status not in rt.statuses:
        raise HTTPException(400, "无效状态")
    r = BusinessRecord(record_type_id=rt.id, title=body.title.strip() or "(未命名)", status=status,
                       owner_id=body.owner_id or u.id, data=data, created_by=u.id)
    db.add(r)
    db.flush()
    check_evidence_rule(db, rt, r.id, status)
    db.add(RecordHistory(target_type="record", target_id=r.id, version=1, changed_by=u.id,
                         diff={"created": {"title": r.title, "status": status, "data": data}}))
    audit(db, u, "record.created", "record", r.id, type=rt.name)
    if r.owner_id != u.id:
        notify(db, r.owner_id, f"你被指定为「{r.title}」负责人", f"/r/{r.id}")
    db.commit()
    return _rec(r, _names(db))


@router.get("/records/{rid}")
def get_record(rid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    r = get_or_404(db, BusinessRecord, rid)
    rt = db.get(RecordType, r.record_type_id)
    names = _names(db)
    hist = db.query(RecordHistory).filter_by(target_type="record", target_id=rid).order_by(RecordHistory.version.desc()).all()
    tasks = db.query(Task).filter_by(target_type="record", target_id=rid).all()
    return {"record": _rec(r, names), "type": _rt(db, rt),
            "can_edit": can(u, "record.edit_any") or r.owner_id == u.id or r.created_by == u.id,
            "history": [{"version": h.version, "by": names.get(h.changed_by), "at": h.changed_at, "diff": h.diff,
                         "via": h.via} for h in hist],
            "tasks": [{"id": t.id, "title": t.title, "status": t.status} for t in tasks]}


class RecordPatch(BaseModel):
    version: int
    title: str | None = None
    status: str | None = None
    owner_id: str | None = None
    data: dict | None = None


def apply_record_patch(db: Session, u: User, r: BusinessRecord, body: RecordPatch, via: str = "manual") -> dict:
    if not (can(u, "record.edit_any") or r.owner_id == u.id or r.created_by == u.id):
        raise HTTPException(403, "无权编辑此记录")
    if body.version != r.version:
        raise HTTPException(409, {"msg": "记录已被他人修改，请刷新后核对再保存", "current_version": r.version})
    rt = db.get(RecordType, r.record_type_id)
    diff = {}
    if body.title is not None and body.title != r.title:
        diff["title"] = [r.title, body.title]
        r.title = body.title
    if body.owner_id is not None and body.owner_id != r.owner_id:
        diff["owner_id"] = [r.owner_id, body.owner_id]
        r.owner_id = body.owner_id
        notify(db, body.owner_id, f"你被指定为「{r.title}」负责人", f"/r/{r.id}")
    if body.data is not None:
        clean = _validate_data(db, rt, body.data, True)
        merged = {**(r.data or {}), **clean}
        for k, v in clean.items():
            if (r.data or {}).get(k) != v:
                diff[k] = [(r.data or {}).get(k), v]
        r.data = merged
    if body.status is not None and body.status != r.status:
        if body.status not in rt.statuses:
            raise HTTPException(400, "无效状态")
        missing = _missing_required(db, rt, r.data or {})
        if missing:
            raise HTTPException(400, f"变更状态前需补齐必填：{'、'.join(missing)}")
        check_evidence_rule(db, rt, r.id, body.status)
        diff["status"] = [r.status, body.status]
        r.status = body.status
    if not diff:
        return diff
    r.version += 1
    r.updated_at = datetime.now(timezone.utc)
    db.add(RecordHistory(target_type="record", target_id=r.id, version=r.version, changed_by=u.id, diff=diff, via=via))
    audit(db, u, "record.updated", "record", r.id, fields=list(diff), via=via)
    return diff


@router.patch("/records/{rid}")
def update_record(rid: str, body: RecordPatch, u: User = Depends(current_user), db: Session = Depends(get_db)):
    r = get_or_404(db, BusinessRecord, rid)
    apply_record_patch(db, u, r, body)
    db.commit()
    return _rec(r, _names(db))


# ---------- comments / timeline ----------
@router.get("/comments")
def list_comments(target_type: str, target_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    names = _names(db)
    cs = db.query(Comment).filter_by(target_type=target_type, target_id=target_id).order_by(Comment.created_at).all()
    return [{"id": c.id, "user": names.get(c.user_id), "body": c.body, "created_at": c.created_at} for c in cs]


class CommentIn(BaseModel):
    target_type: str
    target_id: str
    body: str


@router.post("/comments")
def add_comment(body: CommentIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if not body.body.strip():
        raise HTTPException(400, "内容为空")
    c = Comment(target_type=body.target_type, target_id=body.target_id, user_id=u.id, body=body.body.strip())
    db.add(c)
    # @提及：@显示名
    for x in db.query(User).filter_by(active=True):
        if f"@{x.display_name}" in body.body and x.id != u.id:
            notify(db, x.id, f"{u.display_name} 在评论中提到了你", "")
    db.commit()
    return {"id": c.id}
