"""Auth, permissions, audit, notifications, crypto helpers."""
import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import DATA_DIR, get_db
from .models import AuditEvent, Notification, User


def _load_key(name: str, gen) -> bytes:
    env = os.environ.get(name.upper())
    if env:
        return env.encode()
    p = DATA_DIR / f"{name}.key"
    if not p.exists():
        p.write_bytes(gen())
        p.chmod(0o600)
    return p.read_bytes().strip()


JWT_SECRET = _load_key("qq_jwt_secret", lambda: secrets.token_urlsafe(48).encode())
FILE_SIGN_KEY = _load_key("qq_file_sign", lambda: secrets.token_urlsafe(48).encode())
# Notebook shared passwords: app-level encryption; set QQ_NOTEBOOK_KEY in production.
_fernet = Fernet(_load_key("qq_notebook_key", Fernet.generate_key))

SESSION_HOURS = int(os.environ.get("QQ_SESSION_HOURS", "12"))

ROLES = {
    "boss": "老板",
    "ops_manager": "运营主管",
    "purchaser": "采购",
    "warehouse": "仓管",
    "production": "生产负责人",
    "quality": "食品安全/质量",
    "hr_finance": "人事/财务",
    "staff": "现场员工",
    "page_admin": "页面管理员",
    "sys_admin": "系统管理员",
}

# 系统管理员默认拥有全部业务权限（便于一人验收与维护）。正式运营如需分权，设置 QQ_ADMIN_ALL_PERMS=0。
ADMIN_ALL_PERMS = os.environ.get("QQ_ADMIN_ALL_PERMS", "1") != "0"

# 团队模式（PRD SEC：小团队精简权限）
#   simple   精简（默认）：老板 / 主管 / 系统管理员 = 全部权限；允许本人核验、本人批准（标注“本人”并留审计）。
#   standard 标准：按岗位分权，关键步骤分人复核。人多了以后在「用户与角色」切换。
ORG = {"mode": os.environ.get("QQ_ORG_MODE", "simple")}
MANAGER_ROLES = {"boss", "ops_manager", "sys_admin"}


def simple_mode() -> bool:
    return ORG["mode"] == "simple"


def role_label(role: str) -> str:
    base = ROLES.get(role, role)
    if role == "sys_admin" and ADMIN_ALL_PERMS:
        return "系统管理员（全部权限）"
    if simple_mode() and role in MANAGER_ROLES:
        return f"{base}（全部权限）"
    return base

# Single permission matrix. Server is the source of truth; the UI only mirrors it.
PERMS: dict[str, set[str]] = {
    "page.edit": {"sys_admin", "page_admin", "ops_manager"},
    "schema.edit": {"sys_admin", "page_admin", "ops_manager"},
    "record.edit_any": {"boss", "ops_manager", "purchaser", "warehouse", "production", "quality", "hr_finance", "sys_admin"},
    "task.manage": {"boss", "ops_manager", "purchaser", "warehouse", "production", "quality", "hr_finance", "sys_admin"},
    "task.verify": {"boss", "ops_manager", "quality"},
    "task.override": {"ops_manager"},
    "evidence.verify": {"boss", "ops_manager", "purchaser", "warehouse", "production", "quality", "hr_finance"},
    "inventory.post": {"warehouse", "ops_manager", "production"},
    "inventory.transfer": {"warehouse", "ops_manager", "purchaser"},
    "inventory.master": {"warehouse", "ops_manager", "purchaser", "sys_admin"},
    "inventory.reserve": {"ops_manager", "warehouse", "purchaser"},
    "production.run": {"production", "ops_manager"},
    "quality.quarantine": {"quality", "ops_manager", "production", "warehouse"},
    "quality.release": {"quality"},
    "notebook.manage": {"boss", "ops_manager", "sys_admin"},
    "audit.view": {"boss", "ops_manager", "sys_admin", "quality"},
    "users.manage": {"sys_admin"},
    "po.manage": {"purchaser", "ops_manager", "boss"},
    "po.approve": {"boss"},
    "po.receive": {"warehouse", "ops_manager", "purchaser", "staff", "production"},
    "finance.reconcile": {"hr_finance", "boss"},
    "settings.purchasing": {"boss"},
    "ingest.apply": {"boss", "ops_manager", "purchaser", "warehouse", "production", "quality", "hr_finance"},
}

EVIDENCE_READ = {
    "normal": None,  # anyone signed in
    "finance": {"boss", "hr_finance", "ops_manager", "purchaser"},
    "hr": {"boss", "hr_finance"},
}


# ---- login brute-force protection (single instance; in-memory) ----
_FAILS: dict[str, list[float]] = {}
MAX_FAILS, WINDOW = 8, 15 * 60


def login_guard(key: str):
    now_t = time.time()
    hits = [t for t in _FAILS.get(key.lower(), []) if t > now_t - WINDOW]
    _FAILS[key.lower()] = hits
    if len(hits) >= MAX_FAILS:
        raise HTTPException(429, "失败次数过多，请 15 分钟后再试")


def login_failed(key: str):
    _FAILS.setdefault(key.lower(), []).append(time.time())


def is_superuser(user: User) -> bool:
    """全部权限：系统管理员（默认），或精简模式下的老板/主管。"""
    return (ADMIN_ALL_PERMS and user.role == "sys_admin") or (simple_mode() and user.role in MANAGER_ROLES)


def self_review_allowed(user: User) -> bool:
    """精简模式下允许本人核验/本人入账（系统标注“本人”）；标准模式要求分人复核。"""
    return simple_mode()


def can(user: User, perm: str) -> bool:
    return is_superuser(user) or user.role in PERMS.get(perm, set())


def require(user: User, perm: str):
    if not can(user, perm):
        raise HTTPException(403, f"权限不足：需要 {perm}")


def can_read_evidence(user: User, sensitivity: str) -> bool:
    allowed = EVIDENCE_READ.get(sensitivity, set())
    return allowed is None or user.role in allowed or is_superuser(user)


# ---- passwords ----
def hash_secret(raw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${h}"


def check_secret(raw: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), 200_000).hex(), h)


def make_token(user: User, method: str = "password") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    return jwt.encode({"sub": user.id, "exp": exp, "m": method}, JWT_SECRET, algorithm="HS256")


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    try:
        payload = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "登录已失效")
    user = db.get(User, payload["sub"])
    if not user or not user.active:  # revocation takes effect on the next request
        raise HTTPException(401, "账号不可用")
    return user


# ---- short-lived signed file URLs (img/iframe can't send headers) ----
def sign_file(version_id: str, user_id: str, ttl: int = 300) -> str:
    exp = int(time.time()) + ttl
    msg = f"{version_id}.{user_id}.{exp}".encode()
    sig = base64.urlsafe_b64encode(hmac.new(FILE_SIGN_KEY, msg, hashlib.sha256).digest()).decode().rstrip("=")
    return f"/api/files/{version_id}?u={user_id}&e={exp}&s={sig}"


def verify_file_sig(version_id: str, user_id: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    msg = f"{version_id}.{user_id}.{exp}".encode()
    good = base64.urlsafe_b64encode(hmac.new(FILE_SIGN_KEY, msg, hashlib.sha256).digest()).decode().rstrip("=")
    return hmac.compare_digest(good, sig)


# ---- notebook encryption ----
def encrypt_secret(raw: str) -> str:
    return _fernet.encrypt(raw.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


# ---- audit / notify ----
def audit(db: Session, user: User | None, action: str, target_type: str | None = None,
          target_id: str | None = None, **detail):
    db.add(AuditEvent(user_id=user.id if user else None, action=action, target_type=target_type,
                      target_id=target_id, detail=detail))


def notify(db: Session, user_id: str | None, title: str, link: str = "", dedup_key: str | None = None):
    if not user_id:
        return
    if dedup_key:
        exists = db.query(Notification).filter_by(user_id=user_id, dedup_key=dedup_key, read=False).first()
        if exists:
            return
    db.add(Notification(user_id=user_id, title=title, link=link, dedup_key=dedup_key))


def get_or_404(db: Session, model, oid, msg: str = "记录不存在"):
    obj = db.get(model, oid) if oid else None
    if obj is None:
        raise HTTPException(404, msg)
    return obj


def user_brief(u: User | None):
    if not u:
        return None
    return {"id": u.id, "name": u.display_name, "role": u.role}
