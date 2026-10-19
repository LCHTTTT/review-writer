"""Normalize provider errors once, keeping prose compatibility at the boundary."""

import json
import re

ERROR_CODES = {
    "empty_response": {"provider_empty_response"},
    "reasoning_only": {"provider_reasoning_only"},
    "model_unavailable": {"model_not_found", "model_not_available", "unsupported_model", "model_access_denied"},
    "quota_exhausted": {"insufficient_quota", "quota_exceeded", "quota_exhausted", "session_quota_exhausted", "billing_hard_limit_reached"},
    "context_limit": {"context_length_exceeded", "max_context_length_exceeded", "request_too_large", "request_body_budget_exhausted"},
    "rate_limited": {"rate_limit_exceeded", "too_many_requests", "too_many_concurrent_requests"},
    "authentication": {"invalid_api_key", "authentication_error", "permission_denied"},
}


def normalize_provider_error(status: int, payload) -> dict:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {"message": payload}
    payload = payload if isinstance(payload, dict) else {}
    error = payload.get("error") or payload.get("detail") or payload
    error = error if isinstance(error, dict) else {"message": str(error)}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    status = details.get("provider_status") or status
    code = str(error.get("code") or error.get("type") or details.get("provider_code") or "").casefold()
    message = str(error.get("message") or "")
    category = next((kind for kind, codes in ERROR_CODES.items()
                     if code in codes or code == "provider_" + kind), "")
    if category == "empty_response" and details.get("reasoning_only") is True:
        category = "reasoning_only"
    # Compatibility for the gateway's previously persisted exact diagnosis.
    if not category and message == "Model provider returned an empty response.":
        category = "empty_response"
    if not category:
        # Legacy relays sometimes omit codes entirely. These are error concepts,
        # not a particular provider's full sentence; unknown wording stays unknown.
        folded = message.casefold()
        if re.search(r"(?:quota|credit|balance).{0,40}(?:exhaust|exceed|insufficient)|insufficient.{0,20}(?:quota|credit|balance)|(?:额度|余额).{0,12}(?:耗尽|不足|超限)", folded):
            category = "quota_exhausted"
        elif re.search(r"context.{0,30}(?:length|limit|long|exceed)|request.body.budget|上下文.{0,12}(?:过长|超限)", folded):
            category = "context_limit"
        elif status in {400, 403, 404, 422} and re.search(r"model.{0,120}(?:not.{0,10}(?:available|found|exist|support)|unavailable|access.{0,10}denied)|(?:模型).{0,30}(?:不可用|不存在|不支持|无权限)", folded):
            category = "model_unavailable"
        elif status in {401, 403}:
            category = "authentication"
        elif status == 429:
            category = "rate_limited"
        elif status == 413:
            category = "context_limit"
        elif status in {408, 500, 502, 503, 504, 524}:
            category = "transient"
        else:
            category = "unknown"
    if category == "empty_response" and details.get("reasoning_only") is True:
        category = "reasoning_only"
    return {"category": category, "code": "PROVIDER_" + category.upper(),
            "provider_code": details.get("provider_code") or code,
            "provider_status": details.get("provider_status") or status, "message": message[:500]}


def provider_error_message(error: dict) -> str:
    if error.get("provider_status") in {408, 504, 524}:
        return MODEL_TIMEOUT_MESSAGE
    return {
        "reasoning_only": "供应商仅返回推理内容，未返回正式答案。本次操作尚未完成，已有结果保留。请先检查模型的思考模式或联系管理员，再显式重试未完成步骤；重试可能产生模型费用。",
        "empty_response": "模型请求已结束，但未返回有效回复。可以从未完成轮次重试；已有操作不会重新执行，重试可能产生模型费用。",
        "outcome_unknown": "模型请求已超过总等待时限，结果尚不确定。系统未重复发送，请先核对供应商结果，避免重复计费。",
        "model_unavailable": "当前配置的模型不存在或未向此服务账号开放，请管理员调整模型或服务分组；重复重试不会解决。",
        "quota_exhausted": "模型提供方额度已耗尽或不足。请恢复额度或重新分配可用会话后重试未完成部分；继续等待不会恢复已失败的请求。",
        "context_limit": "模型请求超过提供方上下文或请求大小限制，需要缩小当前请求后重试。",
        "output_limit": "模型回答达到输出上限，当前结果不完整；已完成的章节会保留，请重试未完成部分。",
        "authentication": "模型服务授权不可用，请检查服务配置后重试。",
        "rate_limited": "模型提供方当前请求过多，请稍后重试未完成部分。",
    }.get(error.get("category"), "文本模型服务暂时不可用，请稍后重试。")


MODEL_TIMEOUT_MESSAGE = "模型服务响应超时，已完成内容已保留。"


def public_model_error(message: str) -> str:
    """Compact known model timeouts; leave unrelated errors and stored diagnostics intact."""
    if "供应商仅返回推理内容" in message or "PROVIDER_REASONING_ONLY" in message:
        return provider_error_message({"category": "reasoning_only"})
    if re.search(r"(?:model|provider|模型|图像服务).{0,100}(?:timed? out|timeout|响应超时)|(?:HTTP|status_code\s*=|provider_status[\"':\s]+)\s*(?:408|504|524)\b|Proxy Read Timeout", message, re.I):
        return MODEL_TIMEOUT_MESSAGE
    return message
