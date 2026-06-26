"""轻量 LLM 客户端 — Agent-hub 的 LLM 调用封装。

不依赖 omniagent，独立实现 OpenAI-compatible chat completion。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def chat_completion(
    model_id: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.3,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: int = 60,
) -> str | None:
    """调用 OpenAI-compatible chat completion API。

    Args:
        model_id: 模型 ID（如 "deepseek-v4-pro", "claude-sonnet-4-6"）
        messages: 消息列表
        max_tokens: 最大生成 token 数
        temperature: 温度
        api_key: API key（默认从环境变量读取）
        base_url: API base URL（默认从环境变量读取）
        timeout: 超时秒数

    Returns:
        模型回复文本，失败返回 None
    """
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("未设置 API key (DEEPSEEK_API_KEY/OPENAI_API_KEY)")
        return None

    base_url = base_url or os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")

    url = f"{base_url.rstrip('/')}/v1/chat/completions"

    body = json.dumps({
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception:
        logger.debug("LLM 调用失败: model=%s", model_id, exc_info=True)
        return None
