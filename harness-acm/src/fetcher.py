"""Codeforces API 封装 + SQLite 缓存 + 结构化日志."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import requests
from pydantic import ValidationError

from metrics import emit_metric
from schemas import (
    CFRatingChange,
    CFSubmission,
    CFUserInfo,
    Profile,
)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DB = ROOT / "cache" / "cf.sqlite"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

API_BASE = "https://codeforces.com/api"
DEFAULT_TIMEOUT = 15
CACHE_TTL_SECONDS = 24 * 3600


logger = logging.getLogger("fetcher")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _handler = logging.FileHandler(LOG_DIR / "fetcher.log")
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)


class FetchError(RuntimeError):
    pass


# ---------- cache ----------

def _init_cache() -> sqlite3.Connection:
    CACHE_DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv ("
        "key TEXT PRIMARY KEY, value TEXT, fetched_at INTEGER)"
    )
    conn.commit()
    return conn


def _cache_get(key: str) -> Any | None:
    conn = _init_cache()
    row = conn.execute(
        "SELECT value, fetched_at FROM kv WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        return None
    value, fetched_at = row
    if time.time() - fetched_at > CACHE_TTL_SECONDS:
        return None
    return json.loads(value)


def _cache_put(key: str, value: Any) -> None:
    conn = _init_cache()
    conn.execute(
        "REPLACE INTO kv (key, value, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(value), int(time.time())),
    )
    conn.commit()


# ---------- core ----------

def _api_call(method: str, **params: Any) -> Any:
    key = f"{method}:{json.dumps(params, sort_keys=True)}"
    key_hash = hashlib.md5(key.encode()).hexdigest()[:10]
    if (cached := _cache_get(key)) is not None:
        logger.info(json.dumps({"event": "cache_hit", "method": method}))
        emit_metric("cache_hit", method=method, key_hash=key_hash)
        return cached

    emit_metric("cache_miss", method=method, key_hash=key_hash)
    url = f"{API_BASE}/{method}"
    last_err: Exception | None = None
    for attempt in range(3):
        t0 = time.time()
        ok = False
        try:
            resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            data = resp.json()
            if data.get("status") != "OK":
                raise FetchError(f"{method}: {data.get('comment')}")
            result = data["result"]
            _cache_put(key, result)
            ok = True
            logger.info(json.dumps({
                "event": "api_ok", "method": method, "attempt": attempt,
            }))
            return result
        except Exception as e:
            last_err = e
            logger.error(json.dumps({
                "event": "api_err", "method": method,
                "attempt": attempt, "error": str(e),
            }))
            time.sleep(1.5 ** attempt)
        finally:
            emit_metric("api_call_done", method=method, attempt=attempt,
                        latency_ms=round((time.time() - t0) * 1000, 1), ok=ok)
    raise FetchError(f"{method} failed after 3 retries: {last_err}")


# ---------- public ----------

def get_user_info(handle: str) -> CFUserInfo:
    data = _api_call("user.info", handles=handle)
    try:
        return CFUserInfo.model_validate(data[0])
    except ValidationError as e:
        raise FetchError(f"user.info schema mismatch: {e}") from e


def get_submissions(handle: str, count: int = 500) -> list[CFSubmission]:
    data = _api_call("user.status", handle=handle, **{"from": 1, "count": count})
    out: list[CFSubmission] = []
    for row in data:
        try:
            out.append(CFSubmission.model_validate(row))
        except ValidationError:
            # 单条损坏不致命，跳过并记录
            logger.error(json.dumps({"event": "sub_parse_err", "id": row.get("id")}))
    return out


def get_rating_history(handle: str) -> list[CFRatingChange]:
    data = _api_call("user.rating", handle=handle)
    return [CFRatingChange.model_validate(r) for r in data]


def fetch_profile(handle: str, submissions: int = 500) -> Profile:
    """一次性抓取完整档案."""
    return Profile(
        user=get_user_info(handle),
        submissions=get_submissions(handle, count=submissions),
        rating_history=get_rating_history(handle),
    )


if __name__ == "__main__":
    import sys
    handle = sys.argv[1] if len(sys.argv) > 1 else "tourist"
    profile = fetch_profile(handle, submissions=50)
    print(f"handle={profile.user.handle} rating={profile.user.rating}")
    print(f"submissions fetched: {len(profile.submissions)}")
    print(f"contests participated: {len(profile.rating_history)}")
