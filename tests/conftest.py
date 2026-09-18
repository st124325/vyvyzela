"""Общие фикстуры тестов.

Тесты идут на синтетическом наборе: он воспроизводит структуру каталогов,
порядок каналов и формат меток выданных данных, поэтому проверяет ровно те же
пути кода, что и реальный прогон.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from firewatch.config import load_config  # noqa: E402
from firewatch.io.chips import ChipDataset  # noqa: E402


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture(scope="session")
def synthetic_dir(tmp_path_factory) -> Path:
    """Небольшой синтетический набор с эталонными масками."""
    target = tmp_path_factory.mktemp("synthetic")
    command = [sys.executable, str(PROJECT_ROOT / "scripts" / "make_synthetic_data.py"),
               "--output", str(target), "--n-af", "4", "--n-bs", "2", "--seed", "7", "--split", "tr"]
    subprocess.run(command, check=True, cwd=PROJECT_ROOT, capture_output=True)
    return target


@pytest.fixture(scope="session")
def dataset(synthetic_dir, config) -> ChipDataset:
    return ChipDataset(synthetic_dir, config)
