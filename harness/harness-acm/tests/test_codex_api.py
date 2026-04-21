"""DashScope API wrapper tests."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _reload_codex_api(monkeypatch, **env):
    for key in (
        "DASHSCOPE_API_KEY",
        "ALIYUN_API_KEY",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
        "DASHSCOPE_JUDGE_MODEL",
        "DASHSCOPE_ENABLE_THINKING",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import codex_api
    return importlib.reload(codex_api)


def test_has_api_key_accepts_aliyun_alias(monkeypatch):
    codex_api = _reload_codex_api(monkeypatch, ALIYUN_API_KEY="alias-key")

    assert codex_api.has_api_key()


def test_generate_text_uses_dashscope_config(monkeypatch):
    calls = {"client": None, "request": None}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create),
            )

        def _create(self, **kwargs):
            calls["request"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=" ok "),
                    )
                ],
            )

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    codex_api = _reload_codex_api(
        monkeypatch,
        DASHSCOPE_API_KEY="dash-key",
        DASHSCOPE_BASE_URL="https://example.test/compatible/v1",
        DASHSCOPE_MODEL="qwen-test",
    )

    text = codex_api.generate_text(
        system_prompt="sys",
        user_content="hello",
        max_output_tokens=123,
    )

    assert text == "ok"
    assert calls["client"] == {
        "api_key": "dash-key",
        "base_url": "https://example.test/compatible/v1",
        "timeout": 30.0,
    }
    assert calls["request"]["model"] == "qwen-test"
    assert calls["request"]["max_tokens"] == 123
    assert calls["request"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    assert "reasoning" not in calls["request"]
