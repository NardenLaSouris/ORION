"""Fond d'écran : poids d'ORION et coût de production estimé (tokens au tarif public, travail d'un développeur)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.wallpaper.project import ai_cost, footprint, project_source  # noqa: E402
from scripts.production_stats import collect  # noqa: E402


def fake_install(tmp_path):
    (tmp_path / "orion").mkdir()
    (tmp_path / "orion" / "a.py").write_text("x = 1\n\ny = 2\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_bytes(b"0" * 1000)  # jamais compté comme du code
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "m.onnx").write_bytes(b"0" * 5000)
    (tmp_path / "data").mkdir()
    return tmp_path


def test_footprint_by_part(tmp_path):
    parts = footprint(fake_install(tmp_path))
    assert parts["python"] == 1000 and parts["models"] == 5000 and parts["code"] == 13


def test_ai_cost_uses_public_prices_and_skips_synthetic_responses():
    stats = {"models": {"claude-opus-5-5": {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                                            "cache_read_input_tokens": 1_000_000, "cache_5m_tokens": 1_000_000,
                                            "cache_1h_tokens": 1_000_000, "responses": 3},
                        "<synthetic>": {"output_tokens": 10**9}}}
    assert round(ai_cost(stats), 2) == round(4 + 20 + 0.2 + 5 + 8, 2)


def test_labour_is_the_hours_really_worked_at_a_developer_net_rate(tmp_path):
    root = fake_install(tmp_path)
    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps({"models": {"claude-opus-5-5": {"output_tokens": 50_000}}, "active_hours": 10}),
                     encoding="utf-8")
    result = project_source(root, stats, 1516.7, 1.0)()
    assert result["hours"] == 10 and result["labour_eur"] == 100 and result["ai_usd"] == 1
    assert result["total_eur"] == 101
    assert project_source(root, tmp_path / "absent.json", 1516.7, 1.0)()["total_eur"] == 0  # sans relevé : rien


def test_token_collection_counts_each_response_once_and_keeps_no_text(tmp_path):
    lines = [{"type": "assistant", "timestamp": "2026-11-03T10:00:00Z",
              "message": {"id": "m1", "model": "claude-opus-5-5", "content": "secret factice",
                          "usage": {"input_tokens": 1, "output_tokens": 5, "cache_read_input_tokens": 0,
                                    "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 7}}}},
             {"type": "assistant", "timestamp": "2026-11-03T10:00:05Z",
              "message": {"id": "m1", "model": "claude-opus-5-5",
                          "usage": {"input_tokens": 1, "output_tokens": 9, "cache_read_input_tokens": 0,
                                    "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 7}}}},
             {"type": "user", "timestamp": "2026-11-03T12:00:00Z"}]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    stats = collect([path])
    opus = stats["models"]["claude-opus-5-5"]
    assert opus["responses"] == 1 and opus["output_tokens"] == 9 and opus["cache_1h_tokens"] == 7
    assert stats["active_hours"] == 0.0  # 5 s comptées, la pause de 2 h non
    assert "secret" not in json.dumps(stats)
