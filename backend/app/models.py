"""Data model (PRD §8). All IDs are stable string IDs; labels can change without breaking links."""
import secrets
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def gid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix):
    return mapped_column(String(32), primary_key=True, default=lambda: gid(prefix))


TS = lambda: mapped_column(DateTime(timezone=True), default=now)  # noqa: E731


# ---------- Workspace ----------
class Site(Base):
    __tablename__ = "sites"
    id: Mapped[str] = _id("site")
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    tz: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _id("usr")
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32))
    lang: Mapped[str] = mapped_column(String(8), default="zh")
    tz: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    agent_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 允许外部 AI 助手代表本人访问
    created_at: Mapped[datetime] = TS()


# ---------- Pages & modules ----------
class Page(Base):
    __tablename__ = "pages"
    id: Mapped[str] = _id("pg")
    name: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str] = mapped_column(String(40), default="layout")
    nav_group: Mapped[str] = mapped_column(String(50), default="业务")
    description: Mapped[str] = mapped_column(Text, default="")
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    visible_roles: Mapped[list] = mapped_column(JSON, default=list)  # empty = all roles
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled | archived
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    draft: Mapped[dict] = mapped_column(JSON, default=lambda: {"modules": []})
    draft_rev: Mapped[int] = mapped_column(Integer, default=1)
    draft_updated_at: Mapped[datetime] = TS()
    published_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = TS()


class PageVersion(Base):
    __tablename__ = "page_versions"
    id: Mapped[str] = _id("pv")
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    layout: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(500), default="")
    published_by: Mapped[str | None] = mapped_column(String(32))
    published_at: Mapped[datetime] = TS()


# ---------- Generic records ----------
class RecordType(Base):
    __tablename__ = "record_types"
    id: Mapped[str] = _id("rt")
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    statuses: Mapped[list] = mapped_column(JSON, default=lambda: ["草稿", "进行中", "待核验", "已完成"])
    # {"已验收": {"purposes": ["验收表", "安装后照片"], "need_approved": false}}
    evidence_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    core: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = TS()


class FieldDefinition(Base):
    __tablename__ = "field_definitions"
    id: Mapped[str] = _id("fd")
    record_type_id: Mapped[str] = mapped_column(ForeignKey("record_types.id"))
    label: Mapped[str] = mapped_column(String(100))
    ftype: Mapped[str] = mapped_column(String(20))  # text longtext number money date select person checkbox
    options: Mapped[list] = mapped_column(JSON, default=list)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    core: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)


class BusinessRecord(Base):
    __tablename__ = "records"
    id: Mapped[str] = _id("rec")
    record_type_id: Mapped[str] = mapped_column(ForeignKey("record_types.id"))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40))
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # keyed by field id
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()
    updated_at: Mapped[datetime] = TS()


class RecordHistory(Base):
    __tablename__ = "record_history"
    id: Mapped[str] = _id("rh")
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    changed_by: Mapped[str | None] = mapped_column(String(32))
    changed_at: Mapped[datetime] = TS()
    diff: Mapped[dict] = mapped_column(JSON)
    via: Mapped[str] = mapped_column(String(40), default="manual")


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[str] = _id("cm")
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = TS()


# ---------- Tasks ----------
class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = _id("tsk")
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20), default="task")  # task | exception | approval
    status: Mapped[str] = mapped_column(String(16), default="todo")  # todo doing blocked review done cancelled
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_step: Mapped[str] = mapped_column(String(500), default="")
    done_criteria: Mapped[str] = mapped_column(String(500), default="")
    requires_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    exception_flag: Mapped[str | None] = mapped_column(String(500), nullable=True)  # override reason
    target_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(60), default="manual")
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()
    updated_at: Mapped[datetime] = TS()


# ---------- Evidence ----------
class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = _id("ev")
    title: Mapped[str] = mapped_column(String(300))
    sensitivity: Mapped[str] = mapped_column(String(16), default="normal")  # normal | finance | hr
    tags: Mapped[list] = mapped_column(JSON, default=list)
    current_version_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()


class EvidenceVersion(Base):
    __tablename__ = "evidence_versions"
    id: Mapped[str] = _id("evv")
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(60), default="upload")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(32))
    uploaded_at: Mapped[datetime] = TS()


class EvidenceLink(Base):
    __tablename__ = "evidence_links"
    id: Mapped[str] = _id("evl")
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"))
    evidence_version_id: Mapped[str] = mapped_column(ForeignKey("evidence_versions.id"))
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    item_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str] = mapped_column(String(60), default="附件")
    verify_status: Mapped[str] = mapped_column(String(16), default="unverified")  # unverified approved rejected superseded
    verified_by: Mapped[str | None] = mapped_column(String(32))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verify_note: Mapped[str] = mapped_column(String(500), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()


# ---------- Checklists ----------
class ChecklistRun(Base):
    __tablename__ = "checklist_runs"
    id: Mapped[str] = _id("ckr")
    page_id: Mapped[str] = mapped_column(String(32))
    module_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200))
    results: Mapped[list] = mapped_column(JSON)  # [{key,label,result,value,note,ok}]
    passed: Mapped[bool] = mapped_column(Boolean)
    submitted_by: Mapped[str] = mapped_column(String(32))
    submitted_at: Mapped[datetime] = TS()
    site_id: Mapped[str | None] = mapped_column(String(32))


# ---------- Inventory ----------
class Warehouse(Base):
    __tablename__ = "warehouses"
    id: Mapped[str] = _id("wh")
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), default="main")  # main | external
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"), nullable=True)


class Sku(Base):
    __tablename__ = "skus"
    id: Mapped[str] = _id("sku")
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="原料")  # 原料 包材 成品 耗材
    base_unit: Mapped[str] = mapped_column(String(16), default="箱")
    conversions: Mapped[dict] = mapped_column(JSON, default=dict)  # {"磅": 20} => 1 base unit = 20 lb
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SafetyStock(Base):
    __tablename__ = "safety_stock"
    __table_args__ = (UniqueConstraint("sku_id", "warehouse_id"),)
    id: Mapped[str] = _id("ss")
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"))
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    qty: Mapped[float] = mapped_column(Float)


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (UniqueConstraint("sku_id", "lot_code"),)
    id: Mapped[str] = _id("lot")
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"))
    lot_code: Mapped[str] = mapped_column(String(64))
    expiry: Mapped[str | None] = mapped_column(String(10), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(16), default="released")  # released | quarantine | pending
    status_reason: Mapped[str] = mapped_column(String(500), default="")
    produced_by_batch: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = TS()


class QualityEvent(Base):
    __tablename__ = "quality_events"
    id: Mapped[str] = _id("qe")
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.id"))
    action: Mapped[str] = mapped_column(String(16))  # quarantine | release
    reason: Mapped[str] = mapped_column(String(1000))
    basis: Mapped[str] = mapped_column(String(1000), default="")
    signature_meaning: Mapped[str] = mapped_column(String(200))
    signed_by: Mapped[str] = mapped_column(String(32))
    signed_at: Mapped[datetime] = TS()


class InventoryPosting(Base):
    """One business action = one posting, guarded by an idempotency key."""
    __tablename__ = "inventory_postings"
    id: Mapped[str] = _id("ip")
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    source_type: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String(1000), default="")
    reverses_posting_id: Mapped[str | None] = mapped_column(String(32))
    reversed_by_posting_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()


class InventoryTxn(Base):
    __tablename__ = "inventory_txns"
    id: Mapped[str] = _id("it")
    posting_id: Mapped[str] = mapped_column(ForeignKey("inventory_postings.id"), index=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"))
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("lots.id"), nullable=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    bucket: Mapped[str] = mapped_column(String(16))  # on_hand | in_transit | unverified
    qty: Mapped[float] = mapped_column(Float)  # signed, base unit
    created_at: Mapped[datetime] = TS()


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[str] = _id("rsv")
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"))
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("lots.id"), nullable=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    qty: Mapped[float] = mapped_column(Float)
    order_ref: Mapped[str] = mapped_column(String(100))
    customer: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()


class Transfer(Base):
    __tablename__ = "transfers"
    id: Mapped[str] = _id("tr")
    code: Mapped[str] = mapped_column(String(40), unique=True)
    from_wh: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    to_wh: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested shipped partial received cancelled
    expected_date: Mapped[str | None] = mapped_column(String(10))
    note: Mapped[str] = mapped_column(String(1000), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()


class TransferLine(Base):
    __tablename__ = "transfer_lines"
    id: Mapped[str] = _id("trl")
    transfer_id: Mapped[str] = mapped_column(ForeignKey("transfers.id"))
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"))
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("lots.id"), nullable=True)
    qty_requested: Mapped[float] = mapped_column(Float)
    qty_shipped: Mapped[float] = mapped_column(Float, default=0)
    qty_received: Mapped[float] = mapped_column(Float, default=0)
    qty_damaged: Mapped[float] = mapped_column(Float, default=0)
    qty_written_off: Mapped[float] = mapped_column(Float, default=0)


class ProductionBatch(Base):
    __tablename__ = "production_batches"
    id: Mapped[str] = _id("pb")
    code: Mapped[str] = mapped_column(String(40), unique=True)
    product_sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"))
    output_lot_id: Mapped[str] = mapped_column(ForeignKey("lots.id"))
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    output_qty: Mapped[float] = mapped_column(Float)
    inputs: Mapped[list] = mapped_column(JSON)  # [{lot_id, sku_id, qty}]
    sop_ref: Mapped[str] = mapped_column(String(100), default="")
    posting_id: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()


# ---------- Notebook ----------
class AccountEntry(Base):
    __tablename__ = "nb_accounts"
    id: Mapped[str] = _id("acc")
    service: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="其他")
    url: Mapped[str] = mapped_column(String(500), default="")
    username: Mapped[str] = mapped_column(String(200), default="")
    account_no: Mapped[str] = mapped_column(String(100), default="")
    purpose: Mapped[str] = mapped_column(String(500), default="")
    owner_id: Mapped[str | None] = mapped_column(String(32))
    backup_owner_id: Mapped[str | None] = mapped_column(String(32))
    mfa_info: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    renewal_date: Mapped[str | None] = mapped_column(String(10))
    last_verified_at: Mapped[str | None] = mapped_column(String(10))
    notes: Mapped[str] = mapped_column(Text, default="")
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet ciphertext, never plaintext
    share_roles: Mapped[list] = mapped_column(JSON, default=list)
    share_users: Mapped[list] = mapped_column(JSON, default=list)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = TS()


class Contact(Base):
    __tablename__ = "nb_contacts"
    id: Mapped[str] = _id("ct")
    name: Mapped[str] = mapped_column(String(100))
    company: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(40), default="其他")
    phone: Mapped[str] = mapped_column(String(60), default="")
    landline: Mapped[str] = mapped_column(String(60), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    wechat: Mapped[str] = mapped_column(String(100), default="")
    language: Mapped[str] = mapped_column(String(40), default="")
    hours: Mapped[str] = mapped_column(String(100), default="")
    emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    internal_owner_id: Mapped[str | None] = mapped_column(String(32))
    last_verified_at: Mapped[str | None] = mapped_column(String(10))
    notes: Mapped[str] = mapped_column(Text, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = TS()


class NotebookNote(Base):
    __tablename__ = "nb_notes"
    id: Mapped[str] = _id("nt")
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="通用")
    body: Mapped[str] = mapped_column(Text, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = TS()


# ---------- Ingestion ----------
class IngestItem(Base):
    __tablename__ = "ingest_items"
    id: Mapped[str] = _id("ing")
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"))
    mode: Mapped[str] = mapped_column(String(16))  # suggest | evidence_only
    status: Mapped[str] = mapped_column(String(20), default="inbox")
    # inbox processing pending_confirm conflict applied evidence_only rejected failed
    source: Mapped[str] = mapped_column(String(30), default="upload")
    context_type: Mapped[str | None] = mapped_column(String(20))
    context_id: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(1000), default="")
    owner_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()
    updated_at: Mapped[datetime] = TS()


class ChangeSet(Base):
    __tablename__ = "change_sets"
    id: Mapped[str] = _id("chg")
    ingest_id: Mapped[str] = mapped_column(ForeignKey("ingest_items.id"))
    target_type: Mapped[str] = mapped_column(String(20))  # record | task
    target_id: Mapped[str] = mapped_column(String(32))
    target_version: Mapped[int] = mapped_column(Integer)
    changes: Mapped[list] = mapped_column(JSON)  # [{field,label,old,new,reason,evidence_loc,decision}]
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed applied partial rejected conflict
    proposed_by: Mapped[str] = mapped_column(String(32))
    proposed_via: Mapped[str] = mapped_column(String(30), default="manual")
    decided_by: Mapped[str | None] = mapped_column(String(32))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = TS()


# ---------- Audit & notifications ----------
class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = _id("au")
    at: Mapped[datetime] = TS()
    user_id: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = _id("nf")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    link: Mapped[str] = mapped_column(String(300), default="")
    dedup_key: Mapped[str | None] = mapped_column(String(120))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = TS()


# ---------- Agent interface (ChatGPT / Claude via MCP or GPT Actions) ----------
class OAuthClient(Base):
    __tablename__ = "oauth_clients"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # client_id
    name: Mapped[str] = mapped_column(String(200))
    secret_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)  # None = public client (PKCE)
    redirect_uris: Mapped[list] = mapped_column(JSON, default=list)
    redirect_prefixes: Mapped[list] = mapped_column(JSON, default=list)  # admin clients only (GPT Actions callback)
    created_via: Mapped[str] = mapped_column(String(16), default="dcr")  # dcr | admin
    created_at: Mapped[datetime] = TS()


class OAuthCode(Base):
    __tablename__ = "oauth_codes"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(32))
    redirect_uri: Mapped[str] = mapped_column(String(1000))
    code_challenge: Mapped[str | None] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(200))
    resource: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentToken(Base):
    __tablename__ = "agent_tokens"
    id: Mapped[str] = _id("agt")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(64))
    client_name: Mapped[str] = mapped_column(String(200))
    access_hash: Mapped[str] = mapped_column(String(64), index=True)
    refresh_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = TS()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
