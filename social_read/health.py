"""Bounded local observations; never a promise that a platform is available.

The persisted per-platform cooldown policy is adapted from Agent Reach 1.6.0,
agent_reach/runtime/dispatch.py at beabda7486e9a666360b18cdb5c0e759c2c4d20f.
Copyright (c) 2025 Agent Eyes. MIT; see licenses/Agent-Reach-MIT.txt.
Storage, partial-result handling and observation records are project additions.
"""
import math
import time

from .model import ReadError

RATE_LIMIT_SECONDS = 3600
ACTION_HINTS = {
    "rate_limited": "等待冷却结束；不要换账号、资料或后端重试。",
    "image_rate_limited": "等待冷却结束；已读正文与图片保留。",
    "login_required": "由本人在原专用浏览器中正常登录。",
    "verification_required": "由本人在平台页面完成验证，停止自动尝试。",
    "access_denied": "平台拒绝访问，请到原页面核对，停止自动尝试。",
    "image_access_denied": "图片访问被拒绝，保留已有内容并到原页面核对。",
    "timeout": "保留已读内容，先检查失败阶段和原浏览器连接。",
    "internal_error": "保留已读内容，由 Agent 检查本地运行状态。",
}


def cooldown(record, at=None):
    """A confirmed rate limit applies to every action sharing this state scope."""
    if not record:
        return None
    left = record["until"] - (time.time() if at is None else at)
    if left <= 0:
        return None
    return {"status": "rate_limited", "retry_after_seconds": max(1, math.ceil(left)),
            "next_action": ACTION_HINTS["rate_limited"]}


def require_available(record):
    paused = cooldown(record)
    if paused:
        error = ReadError("rate_limited", "此平台处于已记录的限流冷却中，未发送新请求。")
        error.retry_after_seconds = paused["retry_after_seconds"]
        raise error


def outcome(value):
    """Inspect structured partial failures without persisting any source text."""
    if isinstance(value, list) and not value:
        return "empty", "empty"
    if not isinstance(value, dict):
        return "success", None
    errors = []
    for key in ("errors", "read_errors", "answer_errors"):
        errors.extend(e for e in value.get(key, []) if isinstance(e, dict))
    codes = [e.get("status") for e in errors]
    for code in ("rate_limited", "image_rate_limited", "verification_required",
                 "login_required", "access_denied", "image_access_denied"):
        if code in codes:
            return "partial", code
    if errors or value.get("coverage", {}).get("ocr_unread", 0):
        return "partial", "partial_content"
    return "success", None
