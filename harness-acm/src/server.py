"""FastAPI HTTP wrapper for the CF-Profiler harness.

A thin gateway over the existing fetcher/aggregator/analyzer/baseline/metrics
modules. Business logic is not duplicated here — every endpoint calls an
existing function and serializes Pydantic models to JSON.

启动:
    PYTHONPATH=src python -m uvicorn server:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from aggregator import aggregate
from analyzer import compute_abilities, generate_narrative_with_judge
from baseline import diff_baseline, load_baseline, save_baseline
from fetcher import FetchError, fetch_profile
from metrics import summarize

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
LOGS_DIR = ROOT / "logs"
SAMPLES_FILE = ROOT / "samples" / "usernames.txt"
JUDGE_LOG = LOGS_DIR / "judge.log"


app = FastAPI(
    title="CF-Profiler Web",
    description="浏览器可视化 Codeforces 选手实力画像 harness 的输出",
    version="0.1.0",
)


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ---------- 通用错误包装 ----------

def _run_pipeline(handle: str, submissions: int):
    """fetch → aggregate → compute_abilities; FetchError → 502, 其它 → 500."""
    try:
        profile = fetch_profile(handle, submissions=submissions)
    except FetchError as e:
        raise HTTPException(status_code=502, detail=f"fetch failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"pipeline error: {e}")
    agg = aggregate(profile)
    report = compute_abilities(agg)
    return profile, agg, report


# ---------- 静态 ----------

@app.get("/", include_in_schema=False)
def index():
    idx = WEB_DIR / "index.html"
    if not idx.exists():
        return JSONResponse(
            {"error": "web/index.html not found. Build the UI first."},
            status_code=503,
        )
    return FileResponse(idx)


@app.get("/api/health")
def health():
    return {"ok": True, "ts": int(time.time())}


# ---------- API ----------

@app.get("/api/samples")
def api_samples() -> list[str]:
    if not SAMPLES_FILE.exists():
        return []
    return [
        line.strip()
        for line in SAMPLES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


@app.get("/api/analyze/{handle}")
def api_analyze(
    handle: str,
    submissions: int = Query(500, ge=10, le=5000),
):
    """fetch → aggregate → compute_abilities. 不跑 AI 评语，亚秒级返回。"""
    profile, agg, report = _run_pipeline(handle, submissions)
    rating_history = [
        {
            "ts": r.ratingUpdateTimeSeconds,
            "newRating": r.newRating,
            "oldRating": r.oldRating,
            "delta": r.newRating - r.oldRating,
            "contestName": r.contestName,
            "rank": r.rank,
        }
        for r in profile.rating_history
    ]
    return {
        "user": profile.user.model_dump(),
        "aggregated": agg.model_dump(),
        "report": report.model_dump(),
        "rating_history": rating_history,
    }


@app.get("/api/narrate/{handle}")
async def api_narrate(
    handle: str,
    submissions: int = Query(500, ge=10, le=5000),
    max_retries: int = Query(2, ge=0, le=5),
):
    """SSE: 每次 judge ensemble 完成 → event: attempt；结束 → event: done。

    把 analyzer.generate_narrative_with_judge 的同步 on_attempt 回调桥接到异步队列上。
    """
    _, _, report = _run_pipeline(handle, submissions)

    async def sse():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _push(item: Optional[dict]) -> None:
            asyncio.run_coroutine_threadsafe(queue.put(item), loop)

        def on_attempt(n: int, judge) -> None:
            _push({
                "type": "attempt",
                "attempt": n,
                "median_score": judge.median_score,
                "individual": [r.model_dump() for r in judge.individual],
                "combined_reason": judge.combined_reason,
            })

        def run() -> None:
            try:
                narrative, judge, trace = generate_narrative_with_judge(
                    report, max_retries=max_retries, on_attempt=on_attempt,
                )
                _push({
                    "type": "done",
                    "narrative": narrative,
                    "judge": {
                        "median_score": judge.median_score,
                        "individual": [r.model_dump() for r in judge.individual],
                        "combined_reason": judge.combined_reason,
                    },
                    "trace": trace,
                })
            except Exception as e:
                _push({"type": "error", "message": str(e)})
            finally:
                _push(None)

        threading.Thread(target=run, daemon=True).start()

        while True:
            item = await queue.get()
            if item is None:
                break
            event_type = item.pop("type")
            yield (
                f"event: {event_type}\n"
                f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/baseline/{handle}")
def api_baseline_get(handle: str):
    data = load_baseline(handle)
    if data is None:
        return {"exists": False, "handle": handle}
    return {"exists": True, **data}


@app.post("/api/baseline/{handle}")
def api_baseline_post(
    handle: str,
    submissions: int = Query(500, ge=10, le=5000),
):
    _, _, report = _run_pipeline(handle, submissions)
    path = save_baseline(report)
    try:
        shown_path = str(path.relative_to(ROOT))
    except ValueError:
        shown_path = str(path)
    return {
        "saved": True,
        "path": shown_path,
        "handle": handle,
        "snapshot_at": report.generated_at,
    }


@app.get("/api/baseline/{handle}/diff")
def api_baseline_diff(
    handle: str,
    threshold: float = Query(5.0, ge=0.0, le=100.0),
    submissions: int = Query(500, ge=10, le=5000),
):
    baseline = load_baseline(handle)
    if baseline is None:
        return {"exists": False, "drifts": []}
    _, _, report = _run_pipeline(handle, submissions)
    drifts = diff_baseline(report, threshold=threshold)
    return {
        "exists": True,
        "threshold": threshold,
        "baseline": baseline,
        "current": {
            "skills": {s.dimension: s.score for s in report.skills},
            "traits": {t.dimension: t.score for t in report.traits},
            "rating": report.overall_rating,
            "peak": report.overall_max_rating,
            "snapshot_at": report.generated_at,
        },
        "drifts": [d.model_dump() for d in drifts],
    }


@app.get("/api/metrics")
def api_metrics(since: Optional[float] = Query(None, ge=0.0)):
    """透传 metrics.summarize。since=小时窗口，None=全部。"""
    return summarize(since_hours=since)


@app.get("/api/logs/judge")
def api_logs_judge(
    handle: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
):
    if not JUDGE_LOG.exists():
        return []
    lines = JUDGE_LOG.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if handle and rec.get("handle") != handle:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out
