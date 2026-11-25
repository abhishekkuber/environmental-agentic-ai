import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")}
STAGES = ["gen_registry.py", "gen_rules.py", "gen_precedents.py", "gen_packs.py"]


def _build(out: Path) -> dict[str, str]:
    for s in STAGES:
        r = subprocess.run([sys.executable, str(ROOT / "data/generators" / s), "--out", str(out)],
                           capture_output=True, text=True, env=ENV)
        assert r.returncode == 0, r.stderr[-2000:]
    return {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for d in ("packs", "labels") for p in sorted((out / d).rglob("*.json"))}


def test_packs_deterministic_and_checked(tmp_path):
    assert _build(tmp_path / "a") == _build(tmp_path / "b")
    res = subprocess.run([sys.executable, str(ROOT / "data/checks/check_packs.py"), "--out", str(tmp_path / "a")],
                         capture_output=True, text=True, env=ENV)
    assert res.returncode == 0, res.stdout[-3000:]
