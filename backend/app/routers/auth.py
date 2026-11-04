from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core import ROLES, PERMS, audit, can, check_secret, current_user, get_or_404, hash_secret, login_failed, login_guard, make_token, require
from ..db import get_db
from ..models import AccountEntry, AuditEvent, BusinessRecord, Contact, Evidence, Notification, Site, Task, User

router = APIRouter(prefix="/api", tags=["auth"])


def me_payload(u: User):
    return {
        "id": u.id, "username": u.username, "name": u.display_name, "role": u.role,
        "role_label": ROLES.get(u.role, u.role), "lang": u.lang, "tz": u.tz,
        "perms": sorted(p for p, roles in PERMS.items() if u.role in roles),
        "has_pin": bool(u.pin_hash), "agent_enabled": u.agent_enabled,
    }


class LoginIn(BaseModel):
    username: str
    password: str


class PinIn(BaseModel):
    username: str
    pin: str


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    login_guard("pw:" + body.username.strip())
    u = db.query(User).filter_by(username=body.username.strip().lower()).first()
    if not u or not u.active or not check_secret(body.password, u.password_hash):
        login_failed("pw:" + body.username.strip())
        audit(db, None, "login.failed", "user", body.username)
        db.commit()
        raise HTTPException(401, "用户名或密码错误")
    audit(db, u, "login", "user", u.id, method="password")
    db.commit()
    return {"token": make_token(u), "user": me_payload(u)}


@router.post("/auth/pin")
def pin_login(body: PinIn, db: Session = Depends(get_db)):
    """Shared-device quick switch. PIN login is limited to field roles."""
    login_guard("pin:" + body.username.strip())
    u = db.query(User).filter_by(username=body.username.strip().lower()).first()
    if not u or not u.active or not check_secret(body.pin, u.pin_hash):
        login_failed("pin:" + body.username.strip())
        raise HTTPException(401, "PIN 错误")
    if u.role in ("boss", "hr_finance", "sys_admin"):
        raise HTTPException(403, "该角色不能使用 PIN 登录")
    audit(db, u, "login", "user", u.id, method="pin")
    db.commit()
    return {"token": make_token(u, "pin"), "user": me_payload(u)}


@router.get("/auth/kiosk-users")
def kiosk_users(db: Session = Depends(get_db)):
    us = db.query(User).filter(User.active, User.pin_hash.isnot(None)).order_by(User.display_name).all()
    return [{"username": u.username, "name": u.display_name} for u in us if u.role not in ("boss", "hr_finance", "sys_admin")]


@router.get("/me")
def me(u: User = Depends(current_user)):
    return me_payload(u)


class MePatch(BaseModel):
    lang: str | None = None
    tz: str | None = None
    pin: str | None = None
    password: str | None = None


@router.patch("/me")
def update_me(body: MePatch, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.lang in ("zh", "en", "es"):
        u.lang = body.lang
    if body.tz:
        u.tz = body.tz
    if body.pin is not None:
        if not (body.pin.isdigit() and 4 <= len(body.pin) <= 6):
            raise HTTPException(400, "PIN 需为 4-6 位数字")
        u.pin_hash = hash_secret(body.pin)
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(400, "密码至少 8 位")
        u.password_hash = hash_secret(body.password)
        audit(db, u, "password.changed", "user", u.id)
    db.commit()
    return me_payload(u)


# ---------- users ----------
@router.get("/users")
def list_users(u: User = Depends(current_user), db: Session = Depends(get_db)):
    us = db.query(User).order_by(User.display_name).all()
    full = can(u, "users.manage")
    return [{"id": x.id, "name": x.display_name, "role": x.role, "role_label": ROLES.get(x.role, x.role),
             **({"username": x.username, "active": x.active, "lang": x.lang, "tz": x.tz} if full else {})}
            for x in us if x.active or full]


class UserIn(BaseModel):
    username: str
    display_name: str
    role: str
    password: str | None = None
    lang: str = "zh"
    tz: str = "America/Los_Angeles"
    active: bool = True


@router.post("/users")
def create_user(body: UserIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "users.manage")
    if body.role not in ROLES:
        raise HTTPException(400, "未知角色")
    if db.query(User).filter_by(username=body.username.lower()).first():
        raise HTTPException(409, "用户名已存在")
    if not body.password or len(body.password) < 8:
        raise HTTPException(400, "初始密码至少 8 位")
    x = User(username=body.username.lower(), display_name=body.display_name, role=body.role,
             password_hash=hash_secret(body.password), lang=body.lang, tz=body.tz)
    db.add(x)
    audit(db, u, "user.created", "user", x.id, role=body.role)
    db.commit()
    return {"id": x.id}


@router.patch("/users/{uid}")
def update_user(uid: str, body: UserIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "users.manage")
    x = get_or_404(db, User, uid)
    was_active = x.active
    x.display_name, x.role, x.lang, x.tz, x.active = body.display_name, body.role, body.lang, body.tz, body.active
    if body.password:
        x.password_hash = hash_secret(body.password)
    audit(db, u, "user.updated", "user", x.id, role=body.role, active=body.active)
    if was_active and not body.active:
        _offboard(db, u, x)
    db.commit()
    return {"ok": True}


def _offboard(db: Session, actor: User, leaver: User):
    """离职：列出该员工可见的共享密码，生成轮换任务（外部服务改密后再关闭）。"""
    accs = db.query(AccountEntry).filter(AccountEntry.status == "active").all()
    visible = [a for a in accs if leaver.role in (a.share_roles or []) or leaver.id in (a.share_users or [])
               or a.created_by == leaver.id]
    mgr = db.query(User).filter_by(role="ops_manager", active=True).first()
    for a in visible:
        t = Task(title=f"轮换共享密码：{a.service}（{leaver.display_name} 离职）", kind="task",
                 description="请在外部服务完成改密后，再在 Notebook 更新密码并关闭本任务。只改 Notebook 文字不算完成。",
                 owner_id=(a.owner_id or (mgr.id if mgr else None)), target_type="account", target_id=a.id,
                 source="offboarding", requires_evidence=False, created_by=actor.id)
        db.add(t)
    audit(db, actor, "user.offboarded", "user", leaver.id, rotation_tasks=len(visible))


# ---------- notifications ----------
@router.get("/notifications")
def notifications(u: User = Depends(current_user), db: Session = Depends(get_db)):
    ns = db.query(Notification).filter_by(user_id=u.id).order_by(Notification.created_at.desc()).limit(50).all()
    return [{"id": n.id, "title": n.title, "link": n.link, "read": n.read, "created_at": n.created_at} for n in ns]


@router.post("/notifications/read-all")
def read_all(u: User = Depends(current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter_by(user_id=u.id, read=False).update({"read": True})
    db.commit()
    return {"ok": True}


# ---------- misc ----------
@router.get("/sites")
def sites(u: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": s.id, "code": s.code, "name": s.name, "tz": s.tz} for s in db.query(Site).all()]


@router.get("/roles")
def roles(u: User = Depends(current_user)):
    return [{"key": k, "label": v} for k, v in ROLES.items()]


@router.get("/search")
def search(q: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """Authorized metadata search. Passwords are never searchable."""
    like = f"%{q.strip()}%"
    out = []
    for r in db.query(BusinessRecord).filter(BusinessRecord.title.ilike(like)).limit(20):
        out.append({"type": "record", "id": r.id, "title": r.title, "sub": r.status, "link": f"/r/{r.id}"})
    for t in db.query(Task).filter(Task.title.ilike(like)).limit(20):
        out.append({"type": "task", "id": t.id, "title": t.title, "sub": t.status, "link": f"/t/{t.id}"})
    for c in db.query(Contact).filter(or_(Contact.name.ilike(like), Contact.company.ilike(like))).limit(20):
        out.append({"type": "contact", "id": c.id, "title": c.name, "sub": c.company, "link": "/notebook?tab=contacts"})
    for a in db.query(AccountEntry).filter(AccountEntry.service.ilike(like)).limit(20):
        out.append({"type": "account", "id": a.id, "title": a.service, "sub": a.category, "link": "/notebook"})
    from ..core import can_read_evidence
    for e in db.query(Evidence).filter(Evidence.title.ilike(like)).limit(40):
        if can_read_evidence(u, e.sensitivity):
            out.append({"type": "evidence", "id": e.id, "title": e.title, "sub": "证据", "link": f"/evidence/{e.id}"})
    return out


@router.get("/audit")
def audit_list(target_id: str | None = None, limit: int = 200, u: User = Depends(current_user),
               db: Session = Depends(get_db)):
    require(u, "audit.view")
    q = db.query(AuditEvent)
    if target_id:
        q = q.filter_by(target_id=target_id)
    names = {x.id: x.display_name for x in db.query(User).all()}
    return [{"id": a.id, "at": a.at, "user": names.get(a.user_id, a.user_id), "action": a.action,
             "target_type": a.target_type, "target_id": a.target_id, "detail": a.detail}
            for a in q.order_by(AuditEvent.at.desc()).limit(min(limit, 1000))]
