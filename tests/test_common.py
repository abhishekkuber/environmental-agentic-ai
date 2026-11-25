import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data" / "generators"))

import common  # noqa: E402


def test_config_loads():
    cfg = common.load_config()
    assert cfg["seed"] == 20261125
    assert cfg["reference_date"] == "2026-11-20"
    assert cfg["languages"]["secondary_language"] is None


def test_stage_seeds_differ_and_are_stable():
    cfg = common.load_config()
    a = common.stage_rng(cfg, "registry").random()
    b = common.stage_rng(cfg, "registry").random()
    c = common.stage_rng(cfg, "rules").random()
    assert a == b and a != c


def test_faker_deterministic():
    cfg = common.load_config()
    assert common.make_faker(cfg, 1).name() == common.make_faker(cfg, 1).name()


def test_point_at_edge_distance_roundtrip():
    area = {"centre_latitude": 24.7, "centre_longitude": 55.1, "radius_m": 1200}
    for bearing in (0, 73, 180, 291):
        for d in (150, 300.5, 499.9, 650):
            lat, lon = common.point_at_edge_distance(area, bearing, d)
            lat, lon = common.round_coord(lat), common.round_coord(lon)
            # DECIMAL(9,6) rounding costs < 0.1 m
            assert abs(common.edge_distance_m(lat, lon, area) - d) < 0.1
