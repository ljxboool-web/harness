"""Centralized Alibaba Cloud DashScope Chat Completions API wrapper.

The project uses DashScope's OpenAI-compatible Chat Completions endpoint. All external
LLM calls should go through this file so secrets and request shape stay in one
place.
"""
from __future__ import annotations

import os
from typing import Any

from env_loader import load_project_env

load_project_env()

DASHSCOPE_API_KEY_ENV = "DASHSCOPE_API_KEY"
ALIYUN_API_KEY_ENV = "ALIYUN_API_KEY"
DEFAULT_DASHSCOPE_BASE_URL = os.environ.get(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
DEFAULT_DASHSCOPE_MODEL = os.environ.get("DASHSCOPE_MODEL", "qwen-turbo")
DEFAULT_DASHSCOPE_JUDGE_MODEL = os.environ.get(
    "DASHSCOPE_JUDGE_MODEL",
    DEFAULT_DASHSCOPE_MODEL,
)
DEFAULT_DASHSCOPE_TIMEOUT_SECONDS = float(
    os.environ.get("DASHSCOPE_TIMEOUT_SECONDS", "30")
)
DASHSCOPE_ENABLE_THINKING_ENV = os.environ.get("DASHSCOPE_ENABLE_THINKING")


class CodexAPIConfigError(RuntimeError):
    """Raised when the local environment is missing required API config."""


def _get_api_key() -> str | None:
    for env_name in (DASHSCOPE_API_KEY_ENV, ALIYUN_API_KEY_ENV):
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip()
    return None


def has_api_key() -> bool:
    return _get_api_key() is not None


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _dashscope_options(model_name: str) -> dict[str, Any]:
    if DASHSCOPE_ENABLE_THINKING_ENV is not None:
        return {
            "extra_body": {
                "enable_thinking": _env_bool(DASHSCOPE_ENABLE_THINKING_ENV),
            }
        }
    if model_name.startswith("qwen3"):
        return {"extra_body": {"enable_thinking": False}}
    return {}


def _build_client():
    api_key = _get_api_key()
    if not api_key:
        raise CodexAPIConfigError("DASHSCOPE_API_KEY not configured")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise CodexAPIConfigError("openai SDK not installed") from exc
    return OpenAI(
        api_key=api_key,
        base_url=DEFAULT_DASHSCOPE_BASE_URL,
        timeout=DEFAULT_DASHSCOPE_TIMEOUT_SECONDS,
    )


def _completion_text(resp: Any) -> str:
    content = resp.choices[0].message.content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def generate_text(
    *,
    system_prompt: str,
    user_content: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    max_output_tokens: int = 600,
) -> str:
    """Run a plain text DashScope Chat Completions request."""
    client = _build_client()
    _ = reasoning_effort
    model_name = model or DEFAULT_DASHSCOPE_MODEL
    resp = client.chat.completions.create(
        model=model_name,
        max_tokens=max_output_tokens,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        **_dashscope_options(model_name),
    )
    return _completion_text(resp)


def generate_json(
    *,
    system_prompt: str,
    user_content: str,
    schema_name: str,
    schema: dict[str, Any],
    model: str | None = None,
    reasoning_effort: str | None = None,
    max_output_tokens: int = 200,
) -> str:
    """Run a JSON-only DashScope Chat Completions request and return raw text."""
    client = _build_client()
    _ = reasoning_effort
    _ = (schema_name, schema)
    model_name = model or DEFAULT_DASHSCOPE_JUDGE_MODEL
    resp = client.chat.completions.create(
        model=model_name,
        max_tokens=max_output_tokens,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        **_dashscope_options(model_name),
    )
    return _completion_text(resp)
