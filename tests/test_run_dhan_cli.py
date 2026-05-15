"""CLI surface tests for run_dhan.py.

Key invariants:

  - --mode=paper exits 0 (dry-run) and emits the daily audit dir.
  - --mode=live is REFUSED with non-zero exit unless BOTH
        config.MODE == "LIVE" and env MODE=LIVE are set.
  - --mode=backtest with a CSV produces a summary on stdout and exits 0.

We don't exercise the live websocket — that's intentional. The point of
this test is the *refusal* logic, which is the single most expensive
default to get wrong.
"""

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

import run_dhan
import config.strategy_config as cfg


def test_paper_mode_dry_run_exits_zero(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MODE", raising=False)
    exit_code = run_dhan.main(["--mode=paper", "--dry-run"])
    assert exit_code == 0
    audit_dir = tmp_path / "state" / "audit"
    assert audit_dir.exists()
    # At least one date-stamped subdir written.
    assert any(audit_dir.iterdir())


def test_live_mode_refused_without_env_var(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MODE", raising=False)
    # Config-side gate could already say LIVE; what matters is env not set.
    monkeypatch.setattr(cfg, "MODE", "PAPER")
    code = run_dhan.main(["--mode=live"])
    assert code == 3


def test_live_mode_refused_without_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODE", "LIVE")
    monkeypatch.setattr(cfg, "MODE", "PAPER")
    code = run_dhan.main(["--mode=live"])
    assert code == 3


def test_live_mode_passes_gate_when_both_set(monkeypatch, tmp_path):
    """When both gates align, the loop progresses past the refusal but
    is currently parked behind an explicit safety stop (no order-update
    WebSocket subscription wired yet). The expected code is the
    parking exit (4), NOT the refusal exit (3) — the test pins that we
    got past the refusal logic correctly.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODE", "LIVE")
    monkeypatch.setattr(cfg, "MODE", "LIVE")
    code = run_dhan.main(["--mode=live"])
    assert code == 4


def test_backtest_mode_runs_csv(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    rows = [
        {"time": "2026-01-05 09:15", "open": 100, "high": 100, "low": 100, "close": 100},
        {"time": "2026-01-05 09:20", "open": 100, "high": 122, "low": 99, "close": 121},
        {"time": "2026-01-05 09:25", "open": 121, "high": 155, "low": 120, "close": 152},
    ]
    csv_path = tmp_path / "fx.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    code = run_dhan.main(["--mode=backtest", f"--csv={csv_path}"])
    out = capsys.readouterr().out
    assert code == 0
    assert "trades=" in out
