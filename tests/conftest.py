"""Fixtures compartilhadas para os testes automatizados."""

from __future__ import annotations

from pathlib import Path

import pytest

from youshop_radar.data.repository import RadarRepository
from youshop_radar.data.seed import seed_database


@pytest.fixture(scope="session")
def seeded_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("radar_db") / "test_radar.db"
    return seed_database(db_path)


@pytest.fixture()
def repo(seeded_db_path: Path) -> RadarRepository:
    return RadarRepository(seeded_db_path)