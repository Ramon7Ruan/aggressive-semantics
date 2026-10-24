import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discourse_kit.ingest import synth  # noqa: E402
from discourse_kit.schema import coerce  # noqa: E402


@pytest.fixture(scope="session")
def synth_rows() -> list[dict]:
    """合成语料的规范格式行（固定 seed，保证测试可重复）。"""
    return synth.generate(seed=7)


@pytest.fixture(scope="session")
def synth_records(synth_rows):
    return [coerce(r) for r in synth_rows]


@pytest.fixture()
def synth_jsonl(tmp_path, synth_rows) -> Path:
    p = tmp_path / "units.jsonl"
    synth.write_jsonl(str(p), synth_rows)
    return p


@pytest.fixture()
def messy_csv(tmp_path, synth_rows) -> Path:
    p = tmp_path / "units_raw.csv"
    synth.write_csv_messy(str(p), synth_rows)
    return p
