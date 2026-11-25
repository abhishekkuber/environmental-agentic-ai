import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")}
SAMPLE = "P1-028,P2-023,P1-027,RT-02,PF-001"  # hostile, A2 (scans), low-quality scan, hidden text, 30 pages


def _run(args):
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True, env=ENV)
    assert r.returncode == 0, r.stderr[-2000:]


def test_render_deterministic(tmp_path):
    for s in ("gen_registry.py", "gen_rules.py", "gen_precedents.py", "gen_packs.py"):
        _run([str(ROOT / "data/generators" / s), "--out", str(tmp_path)])
    # render in two different folders: output must not depend on paths
    other = tmp_path / "elsewhere" / "deeper"
    shutil.copytree(tmp_path / "packs", other / "packs")
    hashes = []
    for out in (tmp_path, other):
        _run([str(ROOT / "data/generators/render_packs.py"), "--out", str(out), "--packs", SAMPLE])
        hashes.append({str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted((out / "packs").rglob("*.pdf"))})
    assert hashes[0] and hashes[0] == hashes[1]
