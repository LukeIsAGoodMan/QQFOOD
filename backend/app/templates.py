"""Page templates. Each returns a fresh layout; module IDs are generated per page."""
from .models import gid


def _m(type_, title, width="full", **config):
    return {"id": gid("mod"), "type": type_, "title": title, "width": width, "hidden": False, "version": 1,
            "config": config}


def _blank(db):
    return {"modules": [_m("text", "说明", body="这是一个新页面。点击「编辑页面」添加模块。")]}


def _periodic_check(db):
    ck = _m("checklist", "巡检检查表", daily_required=True, create_task_on_fail=True, items=[
        {"key": "temp", "label": "温度 (°F)", "kind": "number", "min": -10, "max": 0, "requires_evidence": False},
        {"key": "door", "label": "门封完好、无结霜", "kind": "pass_fail", "requires_evidence": False},
        {"key": "floor", "label": "地面清洁、无积水", "kind": "pass_fail", "requires_evidence": True},
    ])
    return {"modules": [
        _m("metric", "今日应填未提交", "third", metric="missing_checklists"),
        _m("metric", "未关闭异常", "third", metric="open_exceptions"),
        _m("metric", "待核验证据", "third", metric="unverified_evidence"),
        ck,
        _m("kanban", "巡检异常与整改", source="tasks", task_filter={"kind": "exception", "source_module": ck["id"]}),
        _m("evidence", "现场照片", "half"),
        _m("text", "执行说明", "half", body="每班开始前完成一次检查。超出范围的数值会自动生成整改任务。"),
    ]}


def _ledger(db):
    return {"modules": [
        _m("form", "新增记录", "half", source="records", record_type_id=None),
        _m("table", "台账", "full", source="records", record_type_id=None, columns=["title", "status", "owner", "updated_at"]),
    ]}


def _task_board(db):
    return {"modules": [
        _m("metric", "逾期任务", "third", metric="overdue_tasks"),
        _m("metric", "受阻任务", "third", metric="blocked_tasks"),
        _m("metric", "待核验任务", "third", metric="review_tasks"),
        _m("kanban", "任务看板", source="tasks", task_filter={}),
    ]}


PAGE_TEMPLATES = {
    "blank": {"name": "空白页面", "description": "只有一个说明模块", "icon": "layout", "layout": _blank},
    "periodic_check": {"name": "周期检查", "description": "检查表 + 异常整改 + 现场照片 + 缺记录提示（例如冷库巡检）",
                       "icon": "clipboard-check", "layout": _periodic_check},
    "ledger": {"name": "台账", "description": "表单 + 表格，绑定一个记录类型", "icon": "table", "layout": _ledger},
    "task_board": {"name": "任务看板", "description": "任务指标 + 看板", "icon": "kanban", "layout": _task_board},
}
