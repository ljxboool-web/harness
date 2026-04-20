"""server.py 测试 — FastAPI TestClient 覆盖所有端点；fetch_profile 用 monkeypatch 桩掉."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient

import baseline
import metrics
import server
from fetcher import FetchError
from schemas import (
    CFRatingChange,
    CFUserInfo,
    Profile,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把 baseline / metrics 文件路径都改到 tmp_path，避免污染仓库."""
    monkeypatch.setattr(baseline, "BASELINE_DIR", tmp_path / "baselines")
    monkeypatch.setattr(metrics, "METRICS_LOG", tmp_path / "metrics.jsonl")


@pytest.fixture
def fake_profile():
    """构造一个迷你 Profile，rating_history 2 场，submissions 空."""
    return Profile(
        user=CFUserInfo(
            handle="fakehandle", rating=2100, maxRating=2300,
            rank="candidate master", maxRank="master",
            contribution=0, registrationTimeSeconds=1_500_000_000,
        ),
        submissions=[],
        rating_history=[
            CFRatingChange(
                contestId=1, contestName="CF Round 1", handle="fakehandle",
                rank=100, oldRating=1900, newRating=2000,
                ratingUpdateTimeSeconds=1_600_000_000,
            ),
            CFRatingChange(
                contestId=2, contestName="CF Round 2", handle="fakehandle",
                rank=80, oldRating=2000, newRating=2100,
                ratingUpdateTimeSeconds=1_600_100_000,
            ),
        ],
    )


@pytest.fixture
def client(monkeypatch, fake_profile):
    """TestClient；默认 fetch_profile 返回 fake_profile."""
    monkeypatch.setattr(server, "fetch_profile", lambda h, submissions=500: fake_profile)
    return TestClient(server.app)


# ---------- happy paths ----------

def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_samples(client):
    resp = client.get("/api/samples")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # 仓库自带的 samples/usernames.txt 至少含 tourist
    assert "tourist" in data


def test_analyze_happy(client):
    resp = client.get("/api/analyze/fakehandle?submissions=50")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["handle"] == "fakehandle"
    assert data["user"]["rating"] == 2100
    # report 必须含 8 维 skills
    assert len(data["report"]["skills"]) == 8
    assert {s["dimension"] for s in data["report"]["skills"]} == {
        "dp", "graph", "math", "greedy",
        "data_structure", "string", "search", "geometry",
    }
    # 5 维 traits
    assert len(data["report"]["traits"]) == 5
    # rating_history 被裁剪为扁平对象
    assert len(data["rating_history"]) == 2
    r0 = data["rating_history"][0]
    assert {"ts", "newRating", "oldRating", "delta", "contestName", "rank"} <= r0.keys()
    assert r0["delta"] == 100


def test_analyze_fetch_error(monkeypatch):
    def boom(handle, submissions=500):
        raise FetchError("simulated 503")
    monkeypatch.setattr(server, "fetch_profile", boom)
    c = TestClient(server.app)
    resp = c.get("/api/analyze/whoever")
    assert resp.status_code == 502
    assert "fetch failed" in resp.json()["detail"]


def test_baseline_get_missing(client):
    resp = client.get("/api/baseline/nobody")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"exists": False, "handle": "nobody"}


def test_baseline_post_then_get(client, tmp_path):
    resp = client.post("/api/baseline/fakehandle?submissions=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["saved"] is True
    assert body["handle"] == "fakehandle"
    # 文件真的写到了隔离目录
    baseline_file = tmp_path / "baselines" / "fakehandle.json"
    assert baseline_file.exists()

    # GET 现在应该看到它
    resp = client.get("/api/baseline/fakehandle")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert data["handle"] == "fakehandle"
    assert "skills" in data


def test_baseline_diff_no_baseline(client):
    resp = client.get("/api/baseline/fakehandle/diff")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"exists": False, "drifts": []}


def test_baseline_diff_after_save(client):
    client.post("/api/baseline/fakehandle?submissions=50")
    resp = client.get("/api/baseline/fakehandle/diff?threshold=5.0&submissions=50")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert data["threshold"] == 5.0
    # 同一份 profile 自身对比应无 drift
    assert data["drifts"] == []
    assert "baseline" in data and "current" in data


def test_metrics_empty(client):
    resp = client.get("/api/metrics?since=1")
    assert resp.status_code == 200
    data = resp.json()
    assert "cache" in data
    assert "total_records" in data


def test_logs_judge_empty(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "JUDGE_LOG", tmp_path / "judge.log")
    resp = client.get("/api/logs/judge")
    assert resp.status_code == 200
    assert resp.json() == []


def test_logs_judge_filters_by_handle(client, monkeypatch, tmp_path):
    log = tmp_path / "judge.log"
    log.write_text(
        '{"attempt":1,"handle":"alice","score":5,"reason":"good"}\n'
        '{"attempt":1,"handle":"bob","score":3,"reason":"meh"}\n'
        '{"attempt":2,"handle":"alice","score":4,"reason":"better"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "JUDGE_LOG", log)

    resp = client.get("/api/logs/judge?handle=alice&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(r["handle"] == "alice" for r in data)
    # 倒序：最新的在前
    assert data[0]["attempt"] == 2


def test_index_without_web_dir(client, monkeypatch, tmp_path):
    """WEB_DIR 不存在时 / 返回 503，不 500."""
    monkeypatch.setattr(server, "WEB_DIR", tmp_path / "nowhere")
    resp = client.get("/")
    assert resp.status_code == 503
