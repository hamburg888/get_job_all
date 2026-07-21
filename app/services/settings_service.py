from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AppSetting
from app.schemas.common import LLMConfigOut, LLMConfigUpdate

logger = logging.getLogger(__name__)

_LLM_CATEGORY = "llm"
_LLM_KEYS = {
    "base_url": "llm.base_url",
    "api_key": "llm.api_key",
    "model": "llm.model",
    "temperature": "llm.temperature",
    "enabled": "llm.enabled",
}
_DEFAULTS = {
    "base_url": "",
    "api_key": "",
    "model": "",
    "temperature": 0.7,
    "enabled": False,
}


def _get_setting(db: Session, key: str) -> AppSetting | None:
    return db.scalar(select(AppSetting).where(AppSetting.key == key))


def _to_bool(value: str, default: bool = False) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"} if value else default


def _to_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value) if value.strip() else default
    except (TypeError, ValueError):
        return default


def _read_values(db: Session) -> dict[str, Any]:
    rows = db.scalars(select(AppSetting).where(AppSetting.category == _LLM_CATEGORY)).all()
    store = {row.key.split(".", 1)[-1]: row.value for row in rows}
    return {
        "base_url": store.get("base_url", _DEFAULTS["base_url"]),
        "api_key": store.get("api_key", _DEFAULTS["api_key"]),
        "model": store.get("model", _DEFAULTS["model"]),
        "temperature": _to_float(store.get("temperature", ""), _DEFAULTS["temperature"]),
        "enabled": _to_bool(store.get("enabled", ""), _DEFAULTS["enabled"]),
    }


def _mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def get_llm_config(db: Session) -> LLMConfigOut:
    values = _read_values(db)
    return LLMConfigOut(
        base_url=values["base_url"],
        api_key_masked=_mask_api_key(values["api_key"]),
        model=values["model"],
        temperature=values["temperature"],
        enabled=values["enabled"],
    )


def update_llm_config(db: Session, payload: LLMConfigUpdate) -> LLMConfigOut:
    for logical_key, raw_value in {
        "base_url": payload.base_url,
        "model": payload.model,
        "enabled": str(payload.enabled),
        "temperature": str(payload.temperature),
    }.items():
        full_key = _LLM_KEYS[logical_key]
        row = _get_setting(db, full_key)
        if row is None:
            row = AppSetting(key=full_key, value=str(raw_value), category=_LLM_CATEGORY)
            db.add(row)
        else:
            row.value = str(raw_value)
    if payload.api_key and payload.api_key.strip():
        row = _get_setting(db, _LLM_KEYS["api_key"])
        if row is None:
            row = AppSetting(key=_LLM_KEYS["api_key"], value=payload.api_key.strip(), category=_LLM_CATEGORY)
            db.add(row)
        else:
            row.value = payload.api_key.strip()
    db.commit()
    return get_llm_config(db)


def get_full_llm_config(db: Session) -> dict[str, Any]:
    values = _read_values(db)
    return values


def test_llm_config(db: Session) -> dict[str, Any]:
    values = _read_values(db)
    base_url = values["base_url"].strip()
    api_key = values["api_key"].strip()
    model = values["model"].strip() or "gpt-3.5-turbo"
    if not base_url:
        return {"success": False, "message": "未配置 LLM base_url"}
    if not api_key:
        return {"success": False, "message": "未配置 LLM api_key"}
    chat_url = base_url.rstrip("/")
    if not chat_url.endswith("/chat/completions"):
        if chat_url.endswith("/v1"):
            chat_url += "/chat/completions"
        elif "/v1/" not in chat_url:
            chat_url += "/v1/chat/completions"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                chat_url,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                    "temperature": 0,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                return {"success": True, "message": "连接成功"}
            return {"success": False, "message": f"LLM 返回 {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:
        logger.warning("LLM test failed: %s", exc)
        return {"success": False, "message": f"连接失败: {exc}"}
