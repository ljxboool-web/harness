"""Phase 1 smoke test — 验证 CF API 和 schema 校验走得通."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fetcher import fetch_profile


def test_fetch_tourist():
    profile = fetch_profile("tourist", submissions=5)
    assert profile.user.handle.lower() == "tourist"
    assert profile.user.rating is not None
    assert len(profile.submissions) <= 5
    # tourist 参加过大量比赛
    assert len(profile.rating_history) > 10
