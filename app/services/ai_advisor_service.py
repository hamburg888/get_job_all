from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import MasterResume, TaskRun
from app.schemas.common import AIAdviseOut, AIAdviseRequest
from app.services.analysis_service import build_analysis_report_pack
from app.services.settings_service import get_full_llm_config
from app.schemas.common import AnalysisReportPackRequest

logger = logging.getLogger(__name__)


def _serialize_resume(db: Session) -> str:
    resume = db.scalar(select(MasterResume).order_by(desc(MasterResume.updated_at)).limit(1))
    if resume is None:
        return "（暂无简历数据）"
    parts = [
        f"姓名/标识: {resume.full_name or '未填写'}",
        f"一句话简介: {resume.headline or '未填写'}",
        f"联系方式: {resume.contact_summary or '未填写'}",
        f"个人总结:\n{resume.summary or '未填写'}",
    ]
    if resume.skills:
        parts.append("技能: " + ", ".join(resume.skills))
    work = resume.work_experiences or []
    if work:
        lines = []
        for item in work[:6]:
            title = item.get("title") or item.get("position") or ""
            company = item.get("company") or item.get("company_name") or ""
            period = f"{item.get('start_date','')} ~ {item.get('end_date','')}".strip()
            lines.append(f"- {title}@{company} ({period})")
        parts.append("工作经历:\n" + "\n".join(lines))
    projects = resume.project_experiences or []
    if projects:
        lines = []
        for item in projects[:6]:
            name = item.get("name") or item.get("title") or ""
            desc_text = item.get("description") or item.get("summary") or ""
            lines.append(f"- {name}: {desc_text[:120]}")
        parts.append("项目经历:\n" + "\n".join(lines))
    edu = resume.education_experiences or []
    if edu:
        lines = []
        for item in edu[:4]:
            school = item.get("school") or item.get("name") or ""
            degree = item.get("degree") or item.get("education") or ""
            major = item.get("major") or item.get("subject") or ""
            lines.append(f"- {school} {degree} {major}".strip())
        parts.append("教育经历:\n" + "\n".join(lines))
    prefs = resume.job_preferences or {}
    if prefs:
        parts.append("求职意向: " + json.dumps(prefs, ensure_ascii=False)[:300])
    return "\n".join(parts)


def _serialize_task_history(db: Session, platform: str | None = None) -> str:
    stmt = select(TaskRun).order_by(desc(TaskRun.started_at)).limit(20)
    if platform:
        stmt = stmt.where(TaskRun.platform == platform)
    runs = db.scalars(stmt).all()
    if not runs:
        return "（暂无历史任务记录）"
    lines = []
    for run in runs:
        cfg = run.config_json or {}
        salary = ""
        if cfg.get("salary_min") and cfg.get("salary_max"):
            salary = f"{cfg['salary_min']}-{cfg['salary_max']}"
        cities = ",".join(cfg.get("cities", []) or [])
        lines.append(
            f"- 平台={run.platform} 状态={run.status} 投递={run.delivered_count} "
            f"失败={run.failed_count} 薪资={salary} 城市={cities} "
            f"关键词={','.join(cfg.get('keywords', []) or [])} 开始={run.started_at}"
        )
    return "\n".join(lines)


def _build_prompt(report_json: dict, resume_text: str, task_history: str) -> str:
    meta = report_json.get("meta", {})
    stats = report_json.get("stats", {})
    strategy = report_json.get("strategy_inputs", {})
    platform = meta.get("platform", "")
    window_days = meta.get("window_days", "")
    date_from = meta.get("date_from", "")
    date_to = meta.get("date_to", "")

    def _compact(value: Any, limit: int = 1200) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text if len(text) <= limit else text[:limit] + "...(截断)"

    return (
        f"你是一位求职策略顾问，正在帮助一位候选人优化投递策略。"
        f"当前分析平台: {platform}，统计窗口: {date_from} ~ {date_to}（共{window_days}天）。\n\n"
        f"【投递数据统计】\n{_compact(stats)}\n\n"
        f"【策略输入（机会/风险方向）】\n{_compact(strategy)}\n\n"
        f"【近期任务历史配置与结果】\n{task_history}\n\n"
        f"【候选人简历摘要】\n{resume_text}\n\n"
        f"请基于以上信息，输出结构化建议，严格按下面的 JSON 字段格式返回（只返回 JSON，不要任何额外说明）：\n"
        "{\n"
        '  "salary_suggestion": "建议下次投递的薪资区间，结合市场数据和候选人能力，给出区间并说明依据",\n'
        '  "timing_suggestion": "建议的最佳投递时间段/频率，结合历史任务投递量和转化数据",\n'
        '  "resume_suggestion": "针对简历的优化建议，指出与目标岗位匹配或不足的点",\n'
        '  "advice_text": "综合建议，融合投递方向、地区选择、关键词优化等"\n'
        "}"
    )


def _parse_advice(content: str) -> AIAdviseOut:
    text = content.strip()
    if text.startswith('```'):
        fence = text.find('\n')
        if fence != -1:
            text = text[fence:]
        end = text.rfind('```')
        if end != -1:
            text = text[:end]
        text = text.strip()
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return AIAdviseOut(
                advice_text=str(obj.get('advice_text', '')).strip(),
                salary_suggestion=str(obj.get('salary_suggestion', '')).strip(),
                timing_suggestion=str(obj.get('timing_suggestion', '')).strip(),
                resume_suggestion=str(obj.get('resume_suggestion', '')).strip(),
                raw_response=content.strip(),
            )
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM advice JSON, falling back to raw text")
    return AIAdviseOut(
        advice_text=content.strip(),
        salary_suggestion='',
        timing_suggestion='',
        resume_suggestion='',
        raw_response=content.strip(),
    )


def build_ai_advice(db: Session, payload: AIAdviseRequest) -> AIAdviseOut:
    values = get_full_llm_config(db)
    base_url = values.get('base_url', '').strip()
    api_key = values.get('api_key', '').strip()
    model = values.get('model', '').strip() or 'gpt-3.5-turbo'
    temperature = float(values.get('temperature', 0.7) or 0.7)
    enabled = values.get('enabled', False)

    if not enabled:
        return AIAdviseOut(
            advice_text='AI 建议功能未开启，请先在"设置"中配置并启用 LLM。',
            raw_response='',
        )
    if not base_url or not api_key:
        return AIAdviseOut(
            advice_text='未配置有效的 LLM base_url 或 api_key，请先在"设置"中配置。',
            raw_response='',
        )

    date_to = payload.date_to or payload.date_from
    report_req = AnalysisReportPackRequest(
        platform=payload.platform,
        report_type=payload.report_type,
        date_basis='delivery',
        date_from=payload.date_from,
        date_to=date_to,
        compare_previous_period=True,
    )
    report_json = build_analysis_report_pack(db, report_req)
    resume_text = _serialize_resume(db)
    task_history = _serialize_task_history(db, platform=payload.platform.value)
    prompt = _build_prompt(report_json, resume_text, task_history)

    chat_url = base_url.rstrip('/')
    if not chat_url.endswith('/chat/completions'):
        if chat_url.endswith('/v1'):
            chat_url += '/chat/completions'
        elif '/v1/' not in chat_url:
            chat_url += '/v1/chat/completions'

    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                chat_url,
                json={
                    'model': model,
                    'messages': [
                        {'role': 'system', 'content': '你是一位专业的求职策略顾问，擅长分析投递数据并给出可执行建议。'},
                        {'role': 'user', 'content': prompt},
                    ],
                    'temperature': temperature,
                },
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data['choices'][0]['message']['content']
            return _parse_advice(content)
    except Exception as exc:
        logger.warning('AI advice call failed: %s', exc)
        return AIAdviseOut(
            advice_text=f'AI 建议请求失败：{exc}',
            raw_response='',
        )
