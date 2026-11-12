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
    kind: Mapped[str] = mapped_column(String(16), default="main")  # main 主场地 | external 外库(3PL) | company 公司其他地点（多伦多/温哥华）
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    # 是否在本系统管账：False = 只作为来源地（如多伦多、温哥华），发货时不扣它的库存、也不检查账面
    tracked: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Sku(Base):
    __tablename__ = "skus"
    id: Mapped[str] = _id("sku")
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="未分类")  # 干货 冻货 原料肉 原料菜 生产用品 公司用品 生产机器（见 categories.py）
    base_unit: Mapped[str] = mapped_column(String(16), default="箱")
    conversions: Mapped[dict] = mapped_column(JSON, default=dict)  # {"磅": 20} => 1 base unit = 20 lb
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    catch_weight: Mapped[bool] = mapped_column(Boolean, default=False)  # 称重品：基本单位为磅，每箱实重不同
    # 包装规格只是参数，随时可改（换供应商、换箱子）；历史单据保留当时的数字
    purchase_pack: Mapped[float | None] = mapped_column(Float, nullable=True)  # 采购：供应商每箱多少基本单位（如 鸡腿肉 40 磅/箱）
    ship_pack: Mapped[float | None] = mapped_column(Float, nullable=True)      # 出货：每箱目标量（如 鸡丁 50 磅/箱）
    active: Mapped[bool] = mapped_column(Boolean, default=True)   # 停用 / 已合并的商品不出现在选择列表里，历史照常可查
    merged_into: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 用途（食品）：resale 转卖（卖给大统华等）| production 自用（腌制、切配用料）；非食品为空。
    # 同一种货两种用途都有时建成两个商品互相关联（twin_id），如「五香粉」与「五香粉（自用）」。
    usage: Mapped[str] = mapped_column(String(16), default="")
    twin_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


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
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)   # 为哪张客户订单生产
    consumed_qty: Mapped[float | None] = mapped_column(Float, nullable=True)  # 原料投入合计（基本单位）
    loss_qty: Mapped[float | None] = mapped_column(Float, nullable=True)      # 生产损耗 = 投入 − 产出
    yield_pct: Mapped[float | None] = mapped_column(Float, nullable=True)     # 实际出成率
    expected_yield_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss_reason: Mapped[str] = mapped_column(String(300), default="")
    boxes: Mapped[int] = mapped_column(Integer, default=0)
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


# ---------- Settings ----------
class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = TS()


# ---------- Procurement (PRD §6.5 PUR-01..03) ----------
class Requisition(Base):
    """采购需求。数量与余量按版本保存（PUR-02），报价挂在具体版本上（PUR-01）。"""
    __tablename__ = "requisitions"
    id: Mapped[str] = _id("rq")
    code: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(20), default="原料包材")  # 设备 耗材 原料包材
    status: Mapped[str] = mapped_column(String(16), default="open")  # open ordered closed cancelled
    current_version_id: Mapped[str | None] = mapped_column(String(32))
    owner_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()


class RequisitionVersion(Base):
    __tablename__ = "requisition_versions"
    id: Mapped[str] = _id("rqv")
    requisition_id: Mapped[str] = mapped_column(ForeignKey("requisitions.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    margin_pct: Mapped[float] = mapped_column(Float, default=0)
    lines: Mapped[list] = mapped_column(JSON)  # [{key, sku_id, item_name, base_qty, unit}] ; 需求量 = base_qty × (1+余量)
    note: Mapped[str] = mapped_column(String(1000), default="")
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()


class Quote(Base):
    __tablename__ = "quotes"
    id: Mapped[str] = _id("qt")
    requisition_id: Mapped[str] = mapped_column(ForeignKey("requisitions.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("requisition_versions.id"))
    supplier: Mapped[str] = mapped_column(String(200))
    supplier_contact_id: Mapped[str | None] = mapped_column(String(32))
    prices: Mapped[dict] = mapped_column(JSON)  # {line_key: unit_price}
    tax: Mapped[float] = mapped_column(Float, default=0)
    freight: Mapped[float] = mapped_column(Float, default=0)
    install_fee: Mapped[float] = mapped_column(Float, default=0)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    valid_until: Mapped[str | None] = mapped_column(String(10))
    note: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active selected rejected
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[str] = _id("po")
    code: Mapped[str] = mapped_column(String(40), unique=True)
    supplier: Mapped[str] = mapped_column(String(200))
    supplier_contact_id: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(20), default="原料包材")
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    requisition_id: Mapped[str | None] = mapped_column(String(32))
    requisition_version_id: Mapped[str | None] = mapped_column(String(32))
    quote_id: Mapped[str | None] = mapped_column(String(32))
    # draft pending_approval approved ordered partial received closed cancelled rejected
    status: Mapped[str] = mapped_column(String(20), default="draft")
    expected_date: Mapped[str | None] = mapped_column(String(10))
    tax: Mapped[float] = mapped_column(Float, default=0)
    freight: Mapped[float] = mapped_column(Float, default=0)
    install_fee: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str] = mapped_column(String(1000), default="")
    approval: Mapped[dict] = mapped_column(JSON, default=dict)  # {required, limit, by, at, meaning, reason}
    close_reason: Mapped[str] = mapped_column(String(1000), default="")
    invoice_no: Mapped[str] = mapped_column(String(100), default="")  # 票据号（发票号/收据号，收据可空）
    invoice_amount: Mapped[float | None] = mapped_column(Float, nullable=True)  # 票据金额
    doc_type: Mapped[str] = mapped_column(String(16), default="invoice")  # invoice 发票 | receipt 收据 | none 无票据（例外）
    exceptions: Mapped[list] = mapped_column(JSON, default=list)  # [{step, reason, by, at}] 例外记录，永久保留
    quick: Mapped[bool] = mapped_column(Boolean, default=False)  # 补录的已完成采购
    purchase_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 小票 / 票据上的日期
    payment_method: Mapped[str] = mapped_column(String(20), default="")  # company_card 公司卡 | cash 现金 | personal 个人垫付 | check 支票 | transfer 转账 | other
    paid_by: Mapped[str] = mapped_column(String(100), default="")  # 个人垫付时谁付的（以后接报销）
    # none invoiced paid_pending reconciled
    payment_status: Mapped[str] = mapped_column(String(20), default="none")
    reconcile: Mapped[dict] = mapped_column(JSON, default=dict)
    owner_id: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()
    updated_at: Mapped[datetime] = TS()

    @property
    def title(self) -> str:
        return f"{self.code} {self.supplier}"


class POLine(Base):
    __tablename__ = "po_lines"
    id: Mapped[str] = _id("pol")
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    sku_id: Mapped[str | None] = mapped_column(ForeignKey("skus.id"), nullable=True)  # 有 SKU 的行收货会入库
    item_name: Mapped[str] = mapped_column(String(300))
    unit: Mapped[str] = mapped_column(String(16), default="箱")
    qty_ordered: Mapped[float] = mapped_column(Float)
    unit_price: Mapped[float] = mapped_column(Float, default=0)
    qty_received: Mapped[float] = mapped_column(Float, default=0)
    qty_rejected: Mapped[float] = mapped_column(Float, default=0)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Receipt(Base):
    """收货单：现场提交（submitted）→ 有签收单后由仓管签名入账（posted）。"""
    __tablename__ = "receipts"
    id: Mapped[str] = _id("rc")
    code: Mapped[str] = mapped_column(String(40), unique=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    warehouse_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="submitted")  # submitted posted voided
    lines: Mapped[list] = mapped_column(JSON)  # [{po_line_id, qty, rejected, reject_reason, lot_code, expiry, counted}]
    note: Mapped[str] = mapped_column(String(1000), default="")
    submitted_by: Mapped[str] = mapped_column(String(32))
    submitted_at: Mapped[datetime] = TS()
    posted_by: Mapped[str | None] = mapped_column(String(32))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posting_id: Mapped[str | None] = mapped_column(String(32))
    signature_meaning: Mapped[str] = mapped_column(String(200), default="")


# ---------- Batch proposals (ChatGPT / manual bulk import, confirmed by a human) ----------
class BatchProposal(Base):
    """批量提议：SKU 主档、期初/盘点数量、加拿大调入。提交时不改任何数据；人在工作台逐行勾选确认后才应用。"""
    __tablename__ = "batch_proposals"
    id: Mapped[str] = _id("bt")
    code: Mapped[str] = mapped_column(String(40), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # sku_upsert | stock_count | inbound
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    rows: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed applied partial rejected
    source: Mapped[str] = mapped_column(String(40), default="manual")  # manual | agent:ChatGPT
    source_note: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()
    decided_by: Mapped[str | None] = mapped_column(String(32))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------- Customers & sales orders (PRD §6.5 ORD-01..03) ----------
class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = _id("cu")
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    vendor_no: Mapped[str] = mapped_column(String(40), default="")  # 我们在对方系统里的供应商号（如大统华 10325）
    invoice_email: Mapped[str] = mapped_column(String(200), default="")
    accounting_name: Mapped[str] = mapped_column(String(200), default="")  # QuickBooks 里的客户名（导出发票用）
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = TS()


class CustomerSite(Base):
    __tablename__ = "customer_sites"
    id: Mapped[str] = _id("cs")
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))          # Chino Hills Store - Kitchen Deli
    store_no: Mapped[str] = mapped_column(String(20), default="")  # 006
    address: Mapped[str] = mapped_column(String(300), default="")
    dept: Mapped[str] = mapped_column(String(100), default="")
    contact: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerItem(Base):
    """客户品项对照：客户的 Item# / 品名 → 我们的 SKU；factor = 每个客户下单单位等于多少个 SKU 基本单位。"""
    __tablename__ = "customer_items"
    id: Mapped[str] = _id("ci")
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    item_no: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(String(300))
    pack_size: Mapped[str] = mapped_column(String(60), default="")
    order_unit: Mapped[str] = mapped_column(String(16), default="箱")  # 箱 / 磅 …
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"))
    factor: Mapped[float] = mapped_column(Float, default=1.0)
    last_price: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = TS()


class SkuAlias(Base):
    """商品对照（一物多码）：同一个我们的 SKU 在外库、供应商、QuickBooks、旧称里的编码/品名。
    库存只记我们的 SKU；客户（大统华）的对照沿用 customer_items。factor = 对方 1 个单位等于多少个我们的基本单位。"""
    __tablename__ = "sku_aliases"
    id: Mapped[str] = _id("al")
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), index=True)
    party_kind: Mapped[str] = mapped_column(String(16))  # warehouse 外库/调货来源 | supplier 供应商 | accounting QuickBooks | other 旧称
    party: Mapped[str] = mapped_column(String(120))  # 仓库编码（SUPREME）/ 供应商名 / QuickBooks / 说明
    code: Mapped[str] = mapped_column(String(80), default="")
    name: Mapped[str] = mapped_column(String(300), default="")
    pack_size: Mapped[str] = mapped_column(String(60), default="")
    unit: Mapped[str] = mapped_column(String(16), default="")
    factor: Mapped[float] = mapped_column(Float, default=1.0)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = TS()


class SalesOrder(Base):
    __tablename__ = "sales_orders"
    id: Mapped[str] = _id("so")
    code: Mapped[str] = mapped_column(String(40), unique=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    site_id: Mapped[str | None] = mapped_column(ForeignKey("customer_sites.id"), nullable=True)
    po_no: Mapped[str] = mapped_column(String(60))
    po_date: Mapped[str | None] = mapped_column(String(10))
    required_date: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(40), default="")   # General / Freezer / 中央厨房
    # confirmed partial shipped delivered closed cancelled
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    exceptions: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS()
    updated_at: Mapped[datetime] = TS()

    @property
    def title(self) -> str:
        return f"{self.code} PO {self.po_no}"


class SOLine(Base):
    __tablename__ = "so_lines"
    id: Mapped[str] = _id("sol")
    order_id: Mapped[str] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    item_no: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(String(300))
    pack_size: Mapped[str] = mapped_column(String(60), default="")
    order_unit: Mapped[str] = mapped_column(String(16), default="箱")
    qty_ordered: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float, default=0)
    sku_id: Mapped[str | None] = mapped_column(ForeignKey("skus.id"), nullable=True)
    factor: Mapped[float] = mapped_column(Float, default=1.0)
    catch_weight: Mapped[bool] = mapped_column(Boolean, default=False)  # 按实重计价（单位为磅）
    qty_shipped: Mapped[float] = mapped_column(Float, default=0)   # 下单单位（称重行=磅）
    boxes_shipped: Mapped[int] = mapped_column(Integer, default=0)
    qty_rejected: Mapped[float] = mapped_column(Float, default=0)
    reject_reason: Mapped[str] = mapped_column(String(300), default="")


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[str] = _id("sh")
    code: Mapped[str] = mapped_column(String(40), unique=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="shipped")  # shipped delivered
    lines: Mapped[list] = mapped_column(JSON)  # [{line_id, qty, weight, boxes:[box ids], picks:[{lot_id, qty}]}]
    posting_id: Mapped[str | None] = mapped_column(String(32))
    shipped_by: Mapped[str] = mapped_column(String(32))
    shipped_at: Mapped[datetime] = TS()
    delivered_by: Mapped[str | None] = mapped_column(String(32))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(1000), default="")


class Box(Base):
    """称重箱：每箱一个标签（编号、SKU、Lot、重量）。库存按磅记在 Lot 上，箱子记录这些磅数装在哪几箱。"""
    __tablename__ = "boxes"
    id: Mapped[str] = _id("bx")
    serial: Mapped[str] = mapped_column(String(40), unique=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), index=True)
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("lots.id"), nullable=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    weight: Mapped[float] = mapped_column(Float)  # SKU 基本单位（磅）
    status: Mapped[str] = mapped_column(String(16), default="in_stock")  # in_stock shipped void
    shipment_id: Mapped[str | None] = mapped_column(String(32))
    packed_by: Mapped[str] = mapped_column(String(32))
    packed_at: Mapped[datetime] = TS()


class Recipe(Base):
    """配方 / 出成率：一个成品由哪些原料做成。per_output = 每产出 1 个成品基本单位需要多少原料（= 1 / 出成率）。"""
    __tablename__ = "recipes"
    id: Mapped[str] = _id("rcp")
    output_sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), unique=True)
    lines: Mapped[list] = mapped_column(JSON)  # [{sku_id, per_output}]
    note: Mapped[str] = mapped_column(String(500), default="")
    updated_by: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = TS()
