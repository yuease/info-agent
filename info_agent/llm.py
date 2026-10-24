"""模型工厂：按 settings.llm_provider 构造对应厂商的 Chat 模型。

为什么要这一层
--------------
把"用哪家模型"收敛成一个开关。业务代码（app/graph/build.py 等）永远只调
get_model()，不需要知道自己在哪家上跑；换模型 = 改一行 .env，不动 Python 代码。

两家客户端的区别
----------------
    deepseek  -> ChatDeepSeek  (langchain-deepseek)
    sensenova -> ChatOpenAI    (langchain-openai；商汤网关走 OpenAI 兼容协议)

两者都是同一套 /chat/completions 形状，所以工具、中间件、流式输出这些上层
逻辑完全不用区分。

"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from info_agent.config import settings
import httpx
from info_agent.metrics import record_request


def _common_kwargs() -> dict[str, Any]:
    """两家客户端共享的采样与重试参数（字段名在这两个类里是一致的）。"""
    return {
        "temperature": settings.temperature,
        "timeout": settings.request_timeout,
        "max_retries": settings.max_retries,
        "http_client": httpx.Client(event_hooks={"request": [record_request]}),
    }


def _build_deepseek() -> ChatDeepSeek:
    """DeepSeek 官方（或自建）端点。base_url 留空时交给 SDK 用默认地址。"""
    if settings.deepseek_api_key is None:
        raise ValueError("deepseek_api_key 未配置，无法构造 DeepSeek 客户端")
    kwargs = _common_kwargs()
    if settings.deepseek_base_url:
        kwargs["base_url"] = settings.deepseek_base_url
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        extra_body={"thinking": {"type": settings.deepseek_thinking}},
        **kwargs,
    )


def _build_sensenova() -> ChatOpenAI:
    """商汤网关。它是 OpenAI 兼容协议，所以用 ChatOpenAI 指过去。

    这里的模型名来自网关注册表（例如 deepseek-v4-flash），不是厂商 SDK 的
    枚举值——所以在网关换模型时，只要改 .env，不用改代码。
    """
    if settings.sensenova_api_key is None:
        raise ValueError("sensenova_api_key 未配置，无法构造商汤客户端")
    return ChatOpenAI(
        model=settings.sensenova_model,
        api_key=settings.sensenova_api_key,
        base_url=settings.sensenova_base_url,
        **_common_kwargs(),
    )


_BUILDERS = {
    "deepseek": _build_deepseek,
    "sensenova": _build_sensenova,
}


@lru_cache(maxsize=None)
def get_model(provider: str | None = None):
    """构造当前提供商的模型客户端。

    provider 显式传入时用它（测试、A/B 对比用），否则读 .env 的 llm_provider。
    结果按 provider 缓存：两家可以同时存在，来回切不会重复构造。

    单测重置：get_model.cache_clear()
    """
    name = (provider or settings.llm_provider).strip().lower()
    try:
        builder = _BUILDERS[name]
    except KeyError:
        raise ValueError(
            f"未知的 llm_provider：{name!r}，可选 {sorted(_BUILDERS)}"
        ) from None
    return builder()
