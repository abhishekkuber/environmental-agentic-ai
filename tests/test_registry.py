import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "generators"))

import common  # noqa: E402


def _build(out: Path) -> dict[str, str]:
    subprocess.run([sys.executable, str(ROOT / "data/generators/gen_registry.py"), "--out", str(out)],
                   check=True, capture_output=True)
    return {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob("*")) if p.is_file()}


def test_registry_is_deterministic(tmp_path):
    assert _build(tmp_path / "a") == _build(tmp_path / "b")


def test_registry_checks_pass(tmp_path):
    _build(tmp_path)
    res = subprocess.run([sys.executable, str(ROOT / "data/checks/check_registry.py"), "--out", str(tmp_path)],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stdout[-3000:]


def test_name_normalisation_a27():
    cfg = common.load_config()
    n = lambda s: common.normalise_name(s, cfg)  # noqa: E731
    assert n("Harper Fabrication LLC") == n("HARPER FABRICATION L.L.C.") == n("Harper Fabrication, L.L.C.")
    assert n("Stone Agro Co.") == n("Stone Agro Company")
    assert n("Vale Metals Est.") == n("Vale Metals Establishment")
    assert n("Harper Fabrication LLC") != n("Harpar Fabrication LLC")  # typos are not normalised away
