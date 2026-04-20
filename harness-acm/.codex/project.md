# CF-Profiler Codex Project

## Session Checklist

- Workdir: `/home/ljxboool/harness/harness-acm`
- Read order: `AGENTS.md` -> `.codex/project.md` -> `docs/USAGE.md`
- Default validation command: `pytest -x`
- If the tree is dirty, do not revert unrelated user changes

## Purpose

Analyze a Codeforces handle and produce:

- 8 algorithm skill scores
- 5 trait scores
- a short three-part narrative
- optional HTTP/UI output

## Architecture Boundary

- `src/fetcher.py`: fetch only
- `src/aggregator.py`: aggregate only
- `src/analyzer.py`: score and narrate from `AggregatedStats`
- `src/cli.py` and `src/server.py`: presentation only

Do not bypass these layers.

## Primary Commands

```bash
pip install -r requirements.txt
pytest -x
python src/cli.py tourist --no-ai
python src/cli.py tourist --check-baseline
python src/metrics.py stats --since 24
PYTHONPATH=src python -m uvicorn server:app --reload --port 8000
```

## Runtime Notes

- Default LLM path is OpenAI Responses API via Codex-compatible models.
- Without `OPENAI_API_KEY`, narrative generation must fall back to the template path.
- Keep narrative format as `【强项】/【弱项】/【建议】` and keep total length within 300 Chinese characters/roughly one short paragraph per section.
- Prefer fixing project rules in docs before adding duplicate agent-facing instructions elsewhere.

## Verification

- After scoring or boundary changes: `pytest -x`
- After prompt or judge changes: run CLI once with and once without `OPENAI_API_KEY`
- For regression-sensitive changes: `python src/baseline.py check tourist --strict`
- For observability changes: `python src/metrics.py stats --since 24`
