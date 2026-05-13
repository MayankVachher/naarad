"""Drift test for REFERENCE.md.

Re-runs ``scripts/gen_reference.py`` and asserts the on-disk file matches.
Fails the build with a clear "re-run the generator" hint if you've
touched config models or commands without regenerating.
"""
from __future__ import annotations

from pathlib import Path

from scripts.gen_reference import OUT_PATH, render


def test_reference_md_in_sync() -> None:
    expected = render()
    actual = Path(OUT_PATH).read_text(encoding="utf-8")
    assert actual == expected, (
        "REFERENCE.md is out of sync with the config models or commands "
        "registry. Re-run `uv run python scripts/gen_reference.py`."
    )
