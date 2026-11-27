import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")}


def _run(script: str, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / script), "--out", str(out)], capture_output=True, text=True, env=ENV)


def _hashes(out: Path) -> dict[str, str]:
    files = sorted((out / "eval").glob("*")) + sorted((out / "templates" / "examples").glob("*"))
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}


def test_eval_deterministic_and_checked(tmp_path):
    for s in ("gen_registry.py", "gen_rules.py", "gen_precedents.py", "gen_packs.py"):
        r = _run(f"data/generators/{s}", tmp_path)
        assert r.returncode == 0, r.stderr[-2000:]
    hashes = []
    for _ in range(2):
        for s in ("gen_eval.py", "render_templates.py"):
            r = _run(f"data/generators/{s}", tmp_path)
            assert r.returncode == 0, r.stderr[-2000:]
        hashes.append(_hashes(tmp_path))
    assert len(hashes[0]) >= 7 + 9 and hashes[0] == hashes[1]
    res = _run("data/checks/check_eval.py", tmp_path)
    assert res.returncode == 0, res.stdout[-3000:]
