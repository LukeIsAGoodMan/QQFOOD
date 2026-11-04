"""Company Notebook: shared accounts & passwords, contacts, notes (PRD v3 §6.5).

Shared-account model: password is stored encrypted (Fernet), visible to the creator, explicitly shared roles/people,
and notebook managers. Reveal/copy is audited without the value. Passwords never appear in list APIs, search,
exports, notifications or AI processing.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core import audit, can, current_user, decrypt_secret, encrypt_secret, get_or_404, require
from ..db import get_db
from ..models import AccountEntry, Contact, EvidenceLink, NotebookNote, Task, User

router = APIRouter(prefix="/api/nb", tags=["notebook"])

DEFAULT_SHARE_ROLES = ["boss", "ops_manager"]


def can_read_pw(u: User, a: AccountEntry) -> bool:
    return (a.created_by == u.id or u.id in (a.share_users or []) or u.role in (a.share_roles or [])
            or can(u, "notebook.manage"))


def can_edit_acc(u: User, a: AccountEntry) -> bool:
    return a.created_by == u.id or can(u, "notebook.manage")


def acc_out(a: AccountEntry, u: User, names: dict):
    return {"id": a.id, "service": a.service, "category": a.category, "url": a.url, "username": a.username,
            "account_no": a.account_no, "purpose": a.purpose, "owner_id": a.owner_id, "owner": names.get(a.owner_id),
            "backup_owner_id": a.backup_owner_id, "backup_owner": names.get(a.backup_owner_id), "mfa_info": a.mfa_info,
            "status": a.status, "renewal_date": a.renewal_date, "last_verified_at": a.last_verified_at,
            "notes": a.notes, "pinned": a.pinned, "has_password": bool(a.password_enc),
            "can_read_password": can_read_pw(u, a), "can_edit": can_edit_acc(u, a),
            "share_roles": a.share_roles, "share_users": a.share_users,
            "share_user_names": [names.get(x, x) for x in a.share_users or []], "updated_at": a.updated_at}


def _names(db):
    return {x.id: x.display_name for x in db.query(User).all()}


@router.get("/accounts")
def accounts(q: str = "", u: User = Depends(current_user), db: Session = Depends(get_db)):
    qry = db.query(AccountEntry)
    if q:
        qry = qry.filter(or_(AccountEntry.service.ilike(f"%{q}%"), AccountEntry.category.ilike(f"%{q}%"),
                             AccountEntry.username.ilike(f"%{q}%")))
    names = _names(db)
    return [acc_out(a, u, names) for a in qry.order_by(AccountEntry.pinned.desc(), AccountEntry.category, AccountEntry.service)]


class AccIn(BaseModel):
    service: str
    category: str = "其他"
    url: str = ""
    username: str = ""
    account_no: str = ""
    purpose: str = ""
    owner_id: str | None = None
    backup_owner_id: str | None = None
    mfa_info: str = ""
    status: str = "active"
    renewal_date: str | None = None
    last_verified_at: str | None = None
    notes: str = ""
    pinned: bool = False
    password: str | None = None  # write-only; None = unchanged, "" = clear
    share_roles: list[str] | None = None
    share_users: list[str] | None = None


@router.post("/accounts")
def create_account(body: AccIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "task.manage")  # 现场员工不建账号条目
    data = body.model_dump(exclude={"password", "share_roles", "share_users"})
    a = AccountEntry(**data, created_by=u.id, share_roles=body.share_roles or list(DEFAULT_SHARE_ROLES),
                     share_users=body.share_users or [])
    if body.password:
        a.password_enc = encrypt_secret(body.password)
    db.add(a)
    db.flush()
    audit(db, u, "account.created", "account", a.id, service=a.service, has_password=bool(body.password))
    db.commit()
    return acc_out(a, u, _names(db))


@router.patch("/accounts/{aid}")
def update_account(aid: str, body: AccIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    a = get_or_404(db, AccountEntry, aid)
    if not can_edit_acc(u, a):
        raise HTTPException(403, "无权编辑此账号")
    changed = []
    for k, v in body.model_dump(exclude={"password", "share_roles", "share_users"}).items():
        if getattr(a, k) != v:
            setattr(a, k, v)
            changed.append(k)
    if body.share_roles is not None or body.share_users is not None:
        if not can(u, "notebook.manage") and a.created_by != u.id:
            raise HTTPException(403, "无权管理共享")
        if body.share_roles is not None:
            a.share_roles = body.share_roles
        if body.share_users is not None:
            a.share_users = body.share_users
        audit(db, u, "account.share_changed", "account", a.id, roles=a.share_roles, users=a.share_users)
    if body.password is not None:
        a.password_enc = encrypt_secret(body.password) if body.password else None
        audit(db, u, "account.password_changed", "account", a.id)  # value never logged
    a.updated_at = datetime.now(timezone.utc)
    if changed:
        audit(db, u, "account.updated", "account", a.id, fields=changed)
    db.commit()
    return acc_out(a, u, _names(db))


@router.post("/accounts/{aid}/reveal")
def reveal(aid: str, purpose: str = "view", u: User = Depends(current_user), db: Session = Depends(get_db)):
    a = get_or_404(db, AccountEntry, aid)
    if not can_read_pw(u, a):
        audit(db, u, "account.password_denied", "account", a.id)
        db.commit()
        raise HTTPException(403, "无权查看此密码")
    if not a.password_enc:
        raise HTTPException(404, "未保存密码")
    audit(db, u, "account.password_copied" if purpose == "copy" else "account.password_viewed", "account", a.id)
    db.commit()
    return {"password": decrypt_secret(a.password_enc)}


# ---------- contacts ----------
def ct_out(c: Contact, names: dict):
    return {k: getattr(c, k) for k in ("id", "name", "company", "title", "category", "phone", "landline", "email",
                                       "wechat", "language", "hours", "emergency", "internal_owner_id",
                                       "last_verified_at", "notes", "pinned", "updated_at")} | {
        "internal_owner": names.get(c.internal_owner_id)}


@router.get("/contacts")
def contacts(q: str = "", u: User = Depends(current_user), db: Session = Depends(get_db)):
    qry = db.query(Contact)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Contact.name.ilike(like), Contact.company.ilike(like), Contact.category.ilike(like),
                             Contact.phone.ilike(like), Contact.email.ilike(like)))
    names = _names(db)
    return [ct_out(c, names) for c in qry.order_by(Contact.pinned.desc(), Contact.category, Contact.name)]


class CtIn(BaseModel):
    name: str
    company: str = ""
    title: str = ""
    category: str = "其他"
    phone: str = ""
    landline: str = ""
    email: str = ""
    wechat: str = ""
    language: str = ""
    hours: str = ""
    emergency: bool = False
    internal_owner_id: str | None = None
    last_verified_at: str | None = None
    notes: str = ""
    pinned: bool = False
    force: bool = False


def _dupes(db: Session, body: CtIn, exclude: str | None = None):
    conds = [Contact.name == body.name]
    if body.phone:
        conds.append(Contact.phone == body.phone)
    if body.email:
        conds.append(Contact.email == body.email)
    q = db.query(Contact).filter(or_(*conds))
    if exclude:
        q = q.filter(Contact.id != exclude)
    return q.all()


@router.post("/contacts")
def create_contact(body: CtIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _dupes(db, body)
    if d and not body.force:
        raise HTTPException(409, {"msg": "疑似重复联系人", "candidates": [ct_out(c, {}) for c in d]})
    c = Contact(**body.model_dump(exclude={"force"}))
    db.add(c)
    db.flush()
    audit(db, u, "contact.created", "contact", c.id, name=c.name)
    db.commit()
    return ct_out(c, _names(db))


@router.patch("/contacts/{cid}")
def update_contact(cid: str, body: CtIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c = get_or_404(db, Contact, cid)
    for k, v in body.model_dump(exclude={"force"}).items():
        setattr(c, k, v)
    c.updated_at = datetime.now(timezone.utc)
    audit(db, u, "contact.updated", "contact", c.id)
    db.commit()
    return ct_out(c, _names(db))


class MergeIn(BaseModel):
    keep_id: str
    merge_id: str
    preview: bool = True


@router.post("/contacts/merge")
def merge_contacts(body: MergeIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """合并重复联系人：先预览；保留方空字段由被合并方补齐，证据关联转移，被合并方删除并留审计。"""
    require(u, "task.manage")
    keep, other = get_or_404(db, Contact, body.keep_id), get_or_404(db, Contact, body.merge_id)
    fill = {}
    for k in ("company", "title", "phone", "landline", "email", "wechat", "language", "hours"):
        if not getattr(keep, k) and getattr(other, k):
            fill[k] = getattr(other, k)
    links = db.query(EvidenceLink).filter_by(target_type="contact", target_id=other.id).all()
    if body.preview:
        return {"fill": fill, "evidence_links_moved": len(links), "notes_appended": bool(other.notes)}
    for k, v in fill.items():
        setattr(keep, k, v)
    if other.notes:
        keep.notes = (keep.notes + "\n" if keep.notes else "") + f"[合并自 {other.name}] {other.notes}"
    for l in links:
        l.target_id = keep.id
    audit(db, u, "contact.merged", "contact", keep.id, merged=other.id, merged_snapshot=ct_out(other, {}))
    db.delete(other)
    db.commit()
    return {"ok": True}


# ---------- notes ----------
def note_out(n: NotebookNote, names):
    return {"id": n.id, "title": n.title, "category": n.category, "body": n.body, "pinned": n.pinned,
            "archived": n.archived, "version": n.version, "by": names.get(n.created_by), "updated_at": n.updated_at}


@router.get("/notes")
def notes(archived: bool = False, u: User = Depends(current_user), db: Session = Depends(get_db)):
    names = _names(db)
    return [note_out(n, names) for n in db.query(NotebookNote).filter_by(archived=archived)
            .order_by(NotebookNote.pinned.desc(), NotebookNote.updated_at.desc())]


class NoteIn(BaseModel):
    title: str
    category: str = "通用"
    body: str = ""
    pinned: bool = False
    archived: bool = False
    version: int | None = None


@router.post("/notes")
def create_note(body: NoteIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    n = NotebookNote(title=body.title, category=body.category, body=body.body, pinned=body.pinned, created_by=u.id)
    db.add(n)
    db.flush()
    audit(db, u, "note.created", "note", n.id)
    db.commit()
    return note_out(n, _names(db))


@router.patch("/notes/{nid}")
def update_note(nid: str, body: NoteIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    n = get_or_404(db, NotebookNote, nid)
    if body.version is not None and body.version != n.version:
        raise HTTPException(409, "笔记已被他人修改，请刷新")
    from ..models import RecordHistory
    db.add(RecordHistory(target_type="note", target_id=n.id, version=n.version, changed_by=u.id,
                         diff={"title": n.title, "body": n.body}))
    n.title, n.category, n.body, n.pinned, n.archived = body.title, body.category, body.body, body.pinned, body.archived
    n.version += 1
    n.updated_at = datetime.now(timezone.utc)
    audit(db, u, "note.updated", "note", n.id)
    db.commit()
    return note_out(n, _names(db))


class ToTaskIn(BaseModel):
    text: str
    owner_id: str | None = None


@router.post("/notes/{nid}/to-task")
def note_to_task(nid: str, body: ToTaskIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    n = get_or_404(db, NotebookNote, nid)
    t = Task(title=body.text[:280], description=f"来自笔记「{n.title}」", owner_id=body.owner_id or u.id,
             target_type="note", target_id=n.id, source="note", created_by=u.id)
    db.add(t)
    db.flush()
    audit(db, u, "task.created", "task", t.id, source="note")
    db.commit()
    return {"task_id": t.id}
