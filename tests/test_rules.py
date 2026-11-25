import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")}


def _run(script: str, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / script), "--out", str(out)],
                          capture_output=True, text=True, env=ENV)


def _hashes(out: Path) -> dict[str, str]:
    return {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((out / "rules").rglob("*")) if p.is_file()}


def test_rules_deterministic(tmp_path):
    for d in ("a", "b"):
        assert _run("data/generators/gen_rules.py", tmp_path / d).returncode == 0
    assert _hashes(tmp_path / "a") == _hashes(tmp_path / "b")


def test_rule_checks_pass(tmp_path):
    assert _run("data/generators/gen_registry.py", tmp_path).returncode == 0
    assert _run("data/generators/gen_rules.py", tmp_path).returncode == 0
    res = _run("data/checks/check_rules.py", tmp_path)
    assert res.returncode == 0, res.stdout[-3000:]
