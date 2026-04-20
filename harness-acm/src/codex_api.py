"""Centralized OpenAI/Codex Responses API wrapper.

All external LLM calls should go through this file so secrets and request shape
stay in one place.
"""
from __future__ import annotations

import os
from typing import Any

from env_loader import load_project_env

load_project_env()

DEFAULT_OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")
DEFAULT_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.3-codex")
DEFAULT_OPENAI_JUDGE_MODEL = os.environ.get("OPENAI_JUDGE_MODEL", DEFAULT_OPENAI_MODEL)
DEFAULT_OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "medium")
DEFAULT_OPENAI_JUDGE_REASONING_EFFORT = os.environ.get("OPENAI_JUDGE_REASONING_EFFORT", "low")


class CodexAPIConfigError(RuntimeError):
    """Raised when the local environment is missing required API config."""


def has_api_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _build_client():
    if not has_api_key():
        raise CodexAPIConfigError("OPENAI_API_KEY not configured")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise CodexAPIConfigError("openai SDK not installed") from exc
    kwargs: dict[str, Any] = {}
    if DEFAULT_OPENAI_BASE_URL:
        kwargs["base_url"] = DEFAULT_OPENAI_BASE_URL
    return OpenAI(**kwargs)


def generate_text(
    *,
    system_prompt: str,
    user_content: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    max_output_tokens: int = 600,
) -> str:
    """Run a plain text Responses API request and return output_text."""
    client = _build_client()
    resp = client.responses.create(
        model=model or DEFAULT_OPENAI_MODEL,
        reasoning={"effort": reasoning_effort or DEFAULT_OPENAI_REASONING_EFFORT},
        max_output_tokens=max_output_tokens,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return resp.output_text.strip()


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
    """Run a JSON-schema constrained Responses API request and return raw text."""
    client = _build_client()
    resp = client.responses.create(
        model=model or DEFAULT_OPENAI_JUDGE_MODEL,
        reasoning={"effort": reasoning_effort or DEFAULT_OPENAI_JUDGE_REASONING_EFFORT},
        max_output_tokens=max_output_tokens,
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return resp.output_text.strip()
