"""Regression tests for the minimum net ROI admission guard."""
from pathlib import Path


def test_ordinary_position_requires_minimum_roi_before_database_insert():
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    guard = source.index('if not allow_unmatched and result["roi"] + 1e-12 < MIN_NET_ROI:')
    exposure = source.index('check_new_position_allowed(con, result["total_stake"])', guard)
    insert = source.index('INSERT INTO positions (', exposure)
    assert guard < exposure < insert
    assert 'raise HTTPException(' in source[guard:exposure]


def test_emergency_unmatched_recording_is_exempt_from_new_position_roi_guard():
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    assert 'if not allow_unmatched and result["roi"] + 1e-12 < MIN_NET_ROI:' in source
    assert 'return _create_position(req, request, allow_unmatched=True)' in source
