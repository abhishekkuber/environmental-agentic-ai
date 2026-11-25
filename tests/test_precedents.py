import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script: str, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / script), "--out", str(out)], capture_output=True, text=True)


def _hashes(out: Path) -> dict[str, str]:
    files = [out / "precedents" / "precedents.jsonl", out / "eval" / "precedents_gold.jsonl"]
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}


def test_precedents_deterministic_and_checked(tmp_path):
    hashes = []
    for d in ("a", "b"):
        out = tmp_path / d
        assert _run("data/generators/gen_registry.py", out).returncode == 0
        assert _run("data/generators/gen_precedents.py", out).returncode == 0
        hashes.append(_hashes(out))
    assert hashes[0] == hashes[1]
    res = _run("data/checks/check_precedents.py", tmp_path / "a")
    assert res.returncode == 0, res.stdout[-3000:]
