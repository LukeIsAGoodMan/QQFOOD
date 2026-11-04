"""Tasks / exceptions, checklist runs (PRD §6.2-6.4)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import audit, can, current_user, get_or_404, notify, require
from ..db import get_db
from ..models import ChecklistRun, EvidenceLink, EvidenceVersion, Page, PageVersion, RecordHistory, Task, User

router = APIRouter(prefix="/api", tags=["tasks"])

STATUSES = ["todo", "doing", "blocked", "review", "done", "cancelled"]
OPEN = ["todo", "doing", "blocked", "review"]


def _ev_state(db: Session, t: Task):
    links = db.query(EvidenceLink).filter_by(target_type="task", target_id=t.id, active=True).all()
    return {"count": len(links), "approved": sum(1 for l in links if l.verify_status == "approved"),
            "unverified": sum(1 for l in links if l.verify_status == "unverified")}


def task_out(db: Session, t: Task, names: dict):
    ev = _ev_state(db, t)
    now = datetime.now(timezone.utc)
    due = t.due_at.replace(tzinfo=timezone.utc) if t.due_at and t.due_at.tzinfo is None else t.due_at
    flags = []
    if t.requires_evidence and ev["count"] == 0 and t.status in ("review", "doing"):
        flags.append("缺证据")
    if due and due < now and t.status in OPEN:
        flags.append("逾期")
    if t.exception_flag:
        flags.append("例外放行")
    return {"id": t.id, "title": t.title, "description": t.description, "kind": t.kind, "status": t.status,
            "owner_id": t.owner_id, "owner": names.get(t.owner_id), "due_at": t.due_at, "next_step": t.next_step,
            "done_criteria": t.done_criteria, "requires_evidence": t.requires_evidence,
            "exception_flag": t.exception_flag, "target_type": t.target_type, "target_id": t.target_id,
            "source": t.source, "version": t.version, "evidence": ev, "flags": flags,
            "created_at": t.created_at, "updated_at": t.updated_at}


def _names(db):
    return {x.id: x.display_name for x in db.query(User).all()}


@router.get("/tasks")
def list_tasks(status: str | None = None, kind: str | None = None, owner: str | None = None,
               source_module: str | None = None, target_id: str | None = None, open_only: bool = False,
               u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Task)
    if status:
        q = q.filter(Task.status.in_(status.split(",")))
    if open_only:
        q = q.filter(Task.status.in_(OPEN))
    if kind:
        q = q.filter_by(kind=kind)
    if owner == "me":
        q = q.filter_by(owner_id=u.id)
    if source_module:
        q = q.filter(Task.source == f"checklist:{source_module}")
    if target_id:
        q = q.filter_by(target_id=target_id)
    names = _names(db)
    return [task_out(db, t, names) for t in q.order_by(Task.due_at.is_(None), Task.due_at, Task.created_at.desc()).limit(500)]


class TaskIn(BaseModel):
    title: str
    description: str = ""
    kind: str = "task"
    owner_id: str | None = None
    due_at: datetime | None = None
    next_step: str = ""
    done_criteria: str = ""
    requires_evidence: bool = False
    target_type: str | None = None
    target_id: str | None = None


@router.post("/tasks")
def create_task(body: TaskIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.kind != "exception":
        require(u, "task.manage")  # 现场员工只能上报异常
    t = Task(**body.model_dump(exclude={"owner_id"}), created_by=u.id, owner_id=body.owner_id or u.id)
    db.add(t)
    db.flush()
    audit(db, u, "task.created", "task", t.id, kind=t.kind)
    if t.owner_id != u.id:
        notify(db, t.owner_id, f"新{'异常' if t.kind == 'exception' else '任务'}：{t.title}", f"/t/{t.id}")
    db.commit()
    return task_out(db, t, _names(db))


@router.get("/tasks/{tid}")
def get_task(tid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    t = get_or_404(db, Task, tid)
    names = _names(db)
    hist = db.query(RecordHistory).filter_by(target_type="task", target_id=tid).order_by(RecordHistory.version.desc()).all()
    return {"task": task_out(db, t, names),
            "can_verify": can(u, "task.verify"), "can_override": can(u, "task.override"),
            "history": [{"version": h.version, "by": names.get(h.changed_by), "at": h.changed_at, "diff": h.diff,
                         "via": h.via} for h in hist]}


class TaskPatch(BaseModel):
    version: int
    title: str | None = None
    description: str | None = None
    status: str | None = None
    owner_id: str | None = None
    due_at: datetime | None = None
    next_step: str | None = None
    done_criteria: str | None = None
    requires_evidence: bool | None = None
    override_reason: str | None = None


def apply_task_patch(db: Session, u: User, t: Task, body: TaskPatch, via: str = "manual") -> dict:
    is_owner = t.owner_id == u.id
    if not (can(u, "task.manage") or is_owner):
        raise HTTPException(403, "无权修改此任务")
    if body.version != t.version:
        raise HTTPException(409, {"msg": "任务已被他人修改，请刷新后再保存", "current_version": t.version})
    diff = {}
    simple = ["title", "description", "owner_id", "due_at", "next_step", "done_criteria", "requires_evidence"]
    for k in simple:
        v = getattr(body, k)
        if v is not None and v != getattr(t, k):
            if k in ("requires_evidence", "done_criteria") and not can(u, "task.manage"):
                raise HTTPException(403, "只有主管可修改完成条件")
            diff[k] = [str(getattr(t, k)) if getattr(t, k) is not None else None, str(v)]
            setattr(t, k, v)
            if k == "owner_id":
                notify(db, v, f"任务转给你：{t.title}", f"/t/{t.id}")
    if body.status and body.status != t.status:
        if body.status not in STATUSES:
            raise HTTPException(400, "无效状态")
        if body.status == "done":
            if not can(u, "task.verify"):
                raise HTTPException(403, "需要核验权限才能标记已完成；请提交为「待核验」")
            ev = _ev_state(db, t)
            if t.requires_evidence and ev["approved"] == 0:
                if body.override_reason and can(u, "task.override"):
                    t.exception_flag = body.override_reason
                    audit(db, u, "task.override", "task", t.id, reason=body.override_reason)
                else:
                    raise HTTPException(409, "此任务要求证据：至少一份证据核验通过后才能完成（或由运营主管填写例外原因）")
        diff["status"] = [t.status, body.status]
        t.status = body.status
        if body.status == "review" and t.created_by and t.created_by != u.id:
            notify(db, t.created_by, f"待核验：{t.title}", f"/t/{t.id}", dedup_key=f"review:{t.id}")
    if diff:
        t.version += 1
        t.updated_at = datetime.now(timezone.utc)
        db.add(RecordHistory(target_type="task", target_id=t.id, version=t.version, changed_by=u.id,
                             diff=diff, via=via))
        audit(db, u, "task.updated", "task", t.id, fields=list(diff), via=via)
    return diff


@router.patch("/tasks/{tid}")
def update_task(tid: str, body: TaskPatch, u: User = Depends(current_user), db: Session = Depends(get_db)):
    t = get_or_404(db, Task, tid)
    apply_task_patch(db, u, t, body)
    db.commit()
    return task_out(db, t, _names(db))


# ---------- checklist runs ----------
def _module_from_published(db: Session, page_id: str, module_id: str) -> tuple[Page, dict]:
    p = get_or_404(db, Page, page_id, "页面不存在")
    v = db.get(PageVersion, p.published_version_id) if p.published_version_id else None
    for m in (v.layout["modules"] if v else []):
        if m["id"] == module_id and m["type"] == "checklist":
            return p, m
    raise HTTPException(404, "检查表模块未发布或不存在")


class ItemResult(BaseModel):
    key: str
    result: str  # pass | fail | na
    value: float | None = None
    note: str = ""
    evidence_version_ids: list[str] = []


class RunIn(BaseModel):
    results: list[ItemResult]


@router.post("/checklists/{page_id}/{module_id}/runs")
def submit_run(page_id: str, module_id: str, body: RunIn, u: User = Depends(current_user),
               db: Session = Depends(get_db)):
    p, m = _module_from_published(db, page_id, module_id)
    cfg = m.get("config", {})
    given = {r.key: r for r in body.results}
    out, failed = [], []
    for it in cfg.get("items", []):
        r = given.get(it["key"])
        if not r:
            raise HTTPException(400, f"「{it['label']}」未填写")
        if r.result == "na" and not r.note.strip():
            raise HTTPException(400, f"「{it['label']}」选择不适用时需填写理由")
        ok = True
        if r.result != "na":
            if it.get("kind") == "number":
                if r.value is None:
                    raise HTTPException(400, f"「{it['label']}」需要实测值")
                lo, hi = it.get("min"), it.get("max")
                ok = (lo is None or r.value >= lo) and (hi is None or r.value <= hi)
            else:
                ok = r.result == "pass"
            if it.get("requires_evidence") and not r.evidence_version_ids:
                raise HTTPException(400, f"「{it['label']}」需要上传证据")
        result = "na" if r.result == "na" else ("pass" if ok else "fail")
        out.append({"key": it["key"], "label": it["label"], "result": result, "value": r.value, "note": r.note, "ok": ok, "range": [it.get("min"), it.get("max")] if it.get("kind") == "number" else None})
        if not ok:
            failed.append(it["label"])
    run = ChecklistRun(page_id=p.id, module_id=module_id, title=m.get("title") or "检查表", results=out,
                       passed=not failed, submitted_by=u.id, site_id=p.site_id)
    db.add(run)
    db.flush()
    for r in body.results:
        for vid in r.evidence_version_ids:
            ver = get_or_404(db, EvidenceVersion, vid, "证据文件不存在")
            db.add(EvidenceLink(evidence_id=ver.evidence_id, evidence_version_id=ver.id, target_type="checklist_run",
                                target_id=run.id, item_key=r.key, purpose="检查项证据", created_by=u.id))
    task_id = None
    if failed and cfg.get("create_task_on_fail", True):
        t = Task(title=f"{m.get('title') or '检查'}不合格：{'、'.join(failed)}", kind="exception",
                 description=f"来自「{p.name}」检查表，提交人 {u.display_name}。整改后上传证据，由核验人复查后关闭。",
                 owner_id=p.owner_id, requires_evidence=True, done_criteria="整改证据核验通过 + 复查",
                 target_type="checklist_run", target_id=run.id, source=f"checklist:{module_id}", created_by=u.id)
        db.add(t)
        db.flush()
        task_id = t.id
        notify(db, p.owner_id, f"检查不合格：{'、'.join(failed)}", f"/t/{t.id}")
    audit(db, u, "checklist.submitted", "checklist_run", run.id, page=p.id, module=module_id, passed=not failed)
    db.commit()
    return {"id": run.id, "passed": not failed, "failed": failed, "task_id": task_id}


@router.get("/checklists/{page_id}/{module_id}/runs")
def list_runs(page_id: str, module_id: str, limit: int = 20, u: User = Depends(current_user),
              db: Session = Depends(get_db)):
    names = _names(db)
    runs = db.query(ChecklistRun).filter_by(module_id=module_id).order_by(ChecklistRun.submitted_at.desc()).limit(limit)
    return [{"id": r.id, "passed": r.passed, "results": r.results, "by": names.get(r.submitted_by),
             "at": r.submitted_at,
             "evidence": db.query(EvidenceLink).filter_by(target_type="checklist_run", target_id=r.id, active=True).count()}
            for r in runs]
