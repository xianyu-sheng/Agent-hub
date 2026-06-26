"""轻量 LLM 客户端 — Agent-hub 的 LLM 调用封装。

不依赖 omniagent，独立实现 OpenAI-compatible chat completion。
支持从 ModelConfigStore 读取配置或从环境变量回退。
所有公共函数均为 async — 使用 asyncio.to_thread 将阻塞 HTTP 调用卸载到线程池。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


async def chat_completion(
    model_id: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.3,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: int = 60,
) -> str | None:
    """调用 OpenAI-compatible chat completion API（异步）。

    使用 asyncio.to_thread 将阻塞的 HTTP 请求卸载到线程池，
    避免阻塞事件循环。

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

    return await asyncio.to_thread(
        _do_chat_completion, model_id, messages, api_key, base_url, max_tokens, temperature, timeout,
    )


async def chat_completion_from_config(
    model_id: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.3,
    timeout: int = 60,
) -> str | None:
    """从 ModelConfigStore 读取模型配置并调用 chat completion（异步）。

    优先使用 models.yaml 中的 api_base 和 api_key，
    若无匹配则回退到环境变量。

    Args:
        model_id: 模型 ID（需匹配 ModelConfigStore 中注册的模型名）
        messages: 消息列表
        max_tokens: 最大生成 token 数
        temperature: 温度
        timeout: 超时秒数

    Returns:
        模型回复文本，失败返回 None
    """
    try:
        from agent_hub.model_config import ModelConfigStore

        store = ModelConfigStore()
        entry = store.get(model_id)

        if entry:
            api_key = entry.resolved_api_key
            base_url = entry.api_base
            if api_key and base_url:
                logger.debug("使用 models.yaml 配置: model=%s api_base=%s", model_id, base_url)
                return await asyncio.to_thread(
                    _do_chat_completion, model_id, messages, api_key, base_url, max_tokens, temperature, timeout,
                )
    except Exception:
        logger.debug("从 models.yaml 读取配置失败，回退到环境变量", exc_info=True)

    # 回退：使用原始方法（环境变量）
    return await chat_completion(
        model_id, messages,
        max_tokens=max_tokens, temperature=temperature, timeout=timeout,
    )


def _do_chat_completion(
    model_id: str,
    messages: list[dict[str, Any]],
    api_key: str,
    base_url: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> str | None:
    """执行实际的 HTTP 请求（同步函数，供 asyncio.to_thread 调用）。"""
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
    except (KeyboardInterrupt, SystemExit):
        # 不吞掉系统信号 — 让进程能正常退出
        raise
    except Exception:
        logger.debug("LLM 调用失败: model=%s", model_id, exc_info=True)
        return None
