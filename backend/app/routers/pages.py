"""Pages, drafts, publish/rollback, copy, module impact (PRD §3)."""
import copy
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core import audit, can, current_user, get_or_404, require
from ..db import get_db
from ..models import (ChecklistRun, Comment, EvidenceLink, FieldDefinition, Page, PageVersion, RecordType, User, gid)
from ..templates import PAGE_TEMPLATES

router = APIRouter(prefix="/api/pages", tags=["pages"])

KNOWN_MODULES = {"metric", "table", "kanban", "form", "checklist", "evidence", "timeline", "text",
                 "stock", "lots", "transfers", "production", "trace", "ingest", "purchasing", "requisitions", "orders", "boxes", "brief"}
RECORD_SOURCED = {"table", "kanban", "form"}


def _visible(page: Page, u: User) -> bool:
    if can(u, "page.edit"):
        return True
    return page.status == "active" and (not page.visible_roles or u.role in page.visible_roles)


def _meta(p: Page, u: User):
    return {"id": p.id, "name": p.name, "icon": p.icon, "nav_group": p.nav_group, "description": p.description,
            "site_id": p.site_id, "owner_id": p.owner_id, "visible_roles": p.visible_roles, "status": p.status,
            "sort_order": p.sort_order, "published_version_id": p.published_version_id,
            "has_unpublished": False, "draft_rev": p.draft_rev, "can_edit": can(u, "page.edit")}


def _published_layout(db: Session, p: Page):
    if not p.published_version_id:
        return None
    v = db.get(PageVersion, p.published_version_id)
    return v.layout if v else None


def normalize_layout(layout: dict) -> dict:
    mods = []
    seen = set()
    for m in (layout or {}).get("modules", []):
        m = dict(m)
        if not m.get("id") or m["id"] in seen:
            m["id"] = gid("mod")
        seen.add(m["id"])
        m.setdefault("type", "text")
        m.setdefault("title", "")
        m.setdefault("width", "full")
        m.setdefault("hidden", False)
        m.setdefault("version", 1)
        m.setdefault("config", {})
        mods.append(m)
    return {"modules": mods}


def check_layout(db: Session, layout: dict) -> list[dict]:
    issues = []
    rts = {r.id: r for r in db.query(RecordType).all()}
    fields = {f.id: f for f in db.query(FieldDefinition).all()}
    for m in layout.get("modules", []):
        name = m.get("title") or m["type"]
        if m["type"] not in KNOWN_MODULES:
            issues.append({"level": "warn", "module_id": m["id"], "msg": f"「{name}」组件类型 {m['type']} 不受支持，将显示只读占位"})
            continue
        cfg = m.get("config", {})
        if m["type"] in RECORD_SOURCED and cfg.get("source", "records") == "records":
            rt = rts.get(cfg.get("record_type_id"))
            if not rt:
                issues.append({"level": "error", "module_id": m["id"], "msg": f"「{name}」未选择或找不到记录类型"})
            elif rt.archived:
                issues.append({"level": "warn", "module_id": m["id"], "msg": f"「{name}」引用的记录类型已归档"})
            for fid in cfg.get("columns", []):
                f = fields.get(fid)
                if fid in ("title", "status", "owner", "updated_at"):
                    continue
                if not f:
                    issues.append({"level": "error", "module_id": m["id"], "msg": f"「{name}」列 {fid} 不存在"})
                elif not f.active:
                    issues.append({"level": "warn", "module_id": m["id"], "msg": f"「{name}」列「{f.label}」已停用"})
        if m["type"] == "checklist" and not cfg.get("items"):
            issues.append({"level": "error", "module_id": m["id"], "msg": f"「{name}」检查表没有检查项"})
        if m["type"] == "metric" and not cfg.get("metric"):
            issues.append({"level": "error", "module_id": m["id"], "msg": f"「{name}」未选择指标"})
    return issues


@router.get("")
def list_pages(all: bool = False, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ps = db.query(Page).order_by(Page.nav_group, Page.sort_order, Page.name).all()
    out = []
    for p in ps:
        if not _visible(p, u):
            continue
        if not all and p.status != "active":
            continue
        pub = _published_layout(db, p)
        meta = _meta(p, u)
        meta["has_unpublished"] = pub != p.draft
        meta["published"] = pub is not None
        out.append(meta)
    return out


@router.get("/templates")
def templates(u: User = Depends(current_user)):
    return [{"key": k, "name": t["name"], "description": t["description"], "icon": t["icon"]}
            for k, t in PAGE_TEMPLATES.items()]


class PageIn(BaseModel):
    name: str
    icon: str = "layout"
    nav_group: str = "业务"
    description: str = ""
    visible_roles: list[str] = []
    template: str = "blank"
    site_id: str | None = None
    owner_id: str | None = None


@router.post("")
def create_page(body: PageIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "page.edit")
    tpl = PAGE_TEMPLATES.get(body.template, PAGE_TEMPLATES["blank"])
    layout = normalize_layout(copy.deepcopy(tpl["layout"](db)))
    p = Page(name=body.name, icon=body.icon or tpl["icon"], nav_group=body.nav_group, description=body.description,
             visible_roles=body.visible_roles, site_id=body.site_id, owner_id=body.owner_id or u.id,
             draft=layout, created_by=u.id)
    db.add(p)
    audit(db, u, "page.created", "page", p.id, template=body.template, name=body.name)
    db.commit()
    return {"id": p.id}


@router.get("/{pid}")
def get_page(pid: str, draft: bool = False, u: User = Depends(current_user), db: Session = Depends(get_db)):
    p = get_or_404(db, Page, pid, "页面不存在")
    if not _visible(p, u):
        raise HTTPException(403, "无权访问此页面")
    pub = _published_layout(db, p)
    meta = _meta(p, u)
    meta["has_unpublished"] = pub != p.draft
    res = {"page": meta, "layout": pub}
    if draft:
        require(u, "page.edit")
        res["draft"] = p.draft
    return res


class PageMeta(BaseModel):
    name: str | None = None
    icon: str | None = None
    nav_group: str | None = None
    description: str | None = None
    visible_roles: list[str] | None = None
    owner_id: str | None = None
    sort_order: int | None = None
    status: str | None = None


@router.patch("/{pid}")
def update_meta(pid: str, body: PageMeta, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "page.edit")
    p = get_or_404(db, Page, pid)
    changes = body.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in ("active", "disabled", "archived"):
        raise HTTPException(400, "无效状态")
    for k, v in changes.items():
        setattr(p, k, v)
    audit(db, u, "page.meta_updated", "page", p.id, **changes)
    db.commit()
    return {"ok": True}


class DraftIn(BaseModel):
    layout: dict
    draft_rev: int


@router.put("/{pid}/draft")
def save_draft(pid: str, body: DraftIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "page.edit")
    p = get_or_404(db, Page, pid)
    if body.draft_rev != p.draft_rev:
        raise HTTPException(409, "草稿已被他人修改，请刷新后再编辑")
    p.draft = normalize_layout(body.layout)
    p.draft_rev += 1
    p.draft_updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"draft": p.draft, "draft_rev": p.draft_rev, "issues": check_layout(db, p.draft)}


@router.get("/{pid}/check")
def check(pid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "page.edit")
    p = get_or_404(db, Page, pid)
    return {"issues": check_layout(db, p.draft)}


class PublishIn(BaseModel):
    note: str = ""


def _publish(db: Session, p: Page, layout: dict, u: User, note: str) -> PageVersion:
    n = (db.query(func.max(PageVersion.version_no)).filter_by(page_id=p.id).scalar() or 0) + 1
    v = PageVersion(page_id=p.id, version_no=n, layout=copy.deepcopy(layout), note=note, published_by=u.id)
    db.add(v)
    db.flush()
    p.published_version_id = v.id
    audit(db, u, "page.published", "page", p.id, version=n, note=note)
    return v


@router.post("/{pid}/publish")
def publish(pid: str, body: PublishIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "page.edit")
    p = get_or_404(db, Page, pid)
    issues = check_layout(db, p.draft)
    errors = [i for i in issues if i["level"] == "error"]
    if errors:
        # 发布失败保留草稿
        raise HTTPException(422, {"msg": "存在错误，未发布；草稿已保留", "issues": issues})
    v = _publish(db, p, p.draft, u, body.note)
    db.commit()
    return {"version_no": v.version_no, "issues": issues}


@router.get("/{pid}/versions")
def versions(pid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "page.edit")
    names = {x.id: x.display_name for x in db.query(User).all()}
    p = get_or_404(db, Page, pid)
    vs = db.query(PageVersion).filter_by(page_id=pid).order_by(PageVersion.version_no.desc()).all()
    return [{"id": v.id, "version_no": v.version_no, "note": v.note, "published_by": names.get(v.published_by),
             "published_at": v.published_at, "modules": len(v.layout.get("modules", [])),
             "current": v.id == p.published_version_id} for v in vs]


class RollbackIn(BaseModel):
    version_id: str


@router.post("/{pid}/rollback")
def rollback(pid: str, body: RollbackIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """回滚只改变配置：把历史版本布局作为新版本发布，草稿同步。业务数据不受影响。"""
    require(u, "page.edit")
    p = get_or_404(db, Page, pid)
    old = get_or_404(db, PageVersion, body.version_id)
    if old.page_id != p.id:
        raise HTTPException(400, "版本不属于此页面")
    v = _publish(db, p, old.layout, u, f"回滚到 v{old.version_no}")
    p.draft = copy.deepcopy(old.layout)
    p.draft_rev += 1
    db.commit()
    return {"version_no": v.version_no}


class CopyIn(BaseModel):
    name: str
    data_mode: str = "reference"  # reference | new_collection


@router.post("/{pid}/copy")
def copy_page(pid: str, body: CopyIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """复制只复制布局/字段配置/规则，不复制业务数据、附件或人员信息。"""
    require(u, "page.edit")
    src = get_or_404(db, Page, pid)
    layout = copy.deepcopy(src.draft)
    rt_map: dict[str, str] = {}
    fd_map: dict[str, str] = {}
    for m in layout["modules"]:
        m["id"] = gid("mod")  # new module instances; evidence on old modules stays with the old page
        cfg = m.get("config", {})
        rid = cfg.get("record_type_id")
        if body.data_mode == "new_collection" and rid:
            if rid not in rt_map:
                rt = db.get(RecordType, rid)
                if rt and not rt.core:
                    nrt = RecordType(name=f"{rt.name}（{body.name}）", description=rt.description,
                                     statuses=list(rt.statuses), evidence_rules=copy.deepcopy(rt.evidence_rules))
                    db.add(nrt)
                    db.flush()
                    for f in db.query(FieldDefinition).filter_by(record_type_id=rid).all():
                        nf = FieldDefinition(record_type_id=nrt.id, label=f.label, ftype=f.ftype, options=list(f.options),
                                             required=f.required, active=f.active, sort_order=f.sort_order)
                        db.add(nf)
                        db.flush()
                        fd_map[f.id] = nf.id
                    rt_map[rid] = nrt.id
            if rid in rt_map:
                cfg["record_type_id"] = rt_map[rid]
                cfg["columns"] = [fd_map.get(c, c) for c in cfg.get("columns", [])]
                if cfg.get("group_field") in fd_map:
                    cfg["group_field"] = fd_map[cfg["group_field"]]
    p = Page(name=body.name, icon=src.icon, nav_group=src.nav_group, description=src.description,
             visible_roles=list(src.visible_roles), site_id=src.site_id, owner_id=u.id,
             draft=normalize_layout(layout), status="active", created_by=u.id)
    db.add(p)
    audit(db, u, "page.copied", "page", p.id, source=src.id, data_mode=body.data_mode)
    db.commit()
    return {"id": p.id}


@router.get("/{pid}/modules/{mid}/impact")
def module_impact(pid: str, mid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "page.edit")
    ev = db.query(EvidenceLink).filter_by(target_type="module", target_id=mid, active=True).count()
    runs = db.query(ChecklistRun).filter_by(module_id=mid).count()
    cms = db.query(Comment).filter_by(target_type="module", target_id=mid).count()
    return {"evidence_links": ev, "checklist_runs": runs, "comments": cms,
            "note": "移除模块仅移除展示，模块下的证据、检查记录和评论保留，可通过恢复历史版本重新显示。"}
