"""Testes de paridade entre o motor Python e a versão web (docs/scoring.js + JSON)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from youshop_radar.data.export_web import build_web_payload
from youshop_radar.data.repository import RadarRepository

ROOT = Path(__file__).resolve().parents[1]
WEB_DATA_PATH = ROOT / "docs" / "dashboard_data.json"
SCORING_JS_PATH = ROOT / "docs" / "scoring.js"


def test_dashboard_json_matches_current_python_engine(repo: RadarRepository) -> None:
    """Garante que docs/dashboard_data.json está sincronizado com o código Python."""
    assert WEB_DATA_PATH.exists(), (
        "docs/dashboard_data.json não encontrado. "
        "Execute 'uv run python -m youshop_radar.data.export_web'."
    )
    published = json.loads(WEB_DATA_PATH.read_text(encoding="utf-8"))
    expected = build_web_payload(repo)
    assert published == expected


def test_js_scoring_matches_python_on_all_parity_cases() -> None:
    """Valida que docs/scoring.js devolve o mesmo score e nível do Python em 100% dos casos."""
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js não disponível no ambiente local para validar docs/scoring.js.")

    script = (
        "const fs = require('fs');"
        "const { evaluateRisk } = require(process.argv[1]);"
        "const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));"
        "const mismatches = [];"
        "for (const c of data.parity_cases) {"
        "  const res = evaluateRisk(c, data.scoring_config);"
        "  if (res.risk_score !== c.expected_risk_score ||"
        "      res.risk_level !== c.expected_risk_level) {"
        "    mismatches.push({ id: c.id, expected: [c.expected_risk_score, c.expected_risk_level],"
        "                      got: [res.risk_score, res.risk_level] });"
        "  }"
        "}"
        "if (mismatches.length > 0) {"
        "  console.error(JSON.stringify(mismatches));"
        "  process.exit(1);"
        "}"
        "console.log(`OK:${data.parity_cases.length}`);"
    )

    proc = subprocess.run(
        [node_bin, "-e", script, str(SCORING_JS_PATH), str(WEB_DATA_PATH)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, f"Divergência Python vs JS: {proc.stderr or proc.stdout}"
    assert proc.stdout.strip().startswith("OK:")