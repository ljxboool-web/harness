"""Minimal local secret/config loader.

Load order:
1. Existing process env
2. ROOT/.env.local
3. ROOT/.env
4. ~/.codex/auth.json (OPENAI_API_KEY only, as a last fallback)

Later sources never override keys that already exist in the environment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_LOADED = False


def _apply_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value:
            continue
        if key in os.environ and os.environ[key].strip():
            continue
        os.environ[key] = value


def _apply_codex_auth() -> None:
    auth_path = Path.home() / ".codex" / "auth.json"
    if (not auth_path.exists()
            or ("OPENAI_API_KEY" in os.environ and os.environ["OPENAI_API_KEY"].strip())):
        return
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    key = data.get("OPENAI_API_KEY")
    if isinstance(key, str) and key.strip():
        os.environ["OPENAI_API_KEY"] = key.strip()


def load_project_env() -> None:
    global _LOADED
    if _LOADED:
        return
    _apply_env_file(ROOT / ".env.local")
    _apply_env_file(ROOT / ".env")
    _apply_codex_auth()
    _LOADED = True
