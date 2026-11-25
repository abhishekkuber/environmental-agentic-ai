"""Shared helpers for every UC1 generator.

Config loading, per-stage deterministic RNG + Faker, geo maths, ID formats,
date helpers and stable JSON writers. Exercise content: fictional data.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import yaml
from faker import Faker

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"
DATA_DIR = REPO_ROOT / "data"
OUTPUT_DIR = DATA_DIR / "output"
SEEDS_MANIFEST = OUTPUT_DIR / "seeds_manifest.json"

EARTH_RADIUS_M = 6_371_008.8  # mean Earth radius


# ------------------------------------------------------------------ config
def load_config(path: Path | str = CONFIG_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def base_arg_parser(description: str) -> argparse.ArgumentParser:
    """Every generator accepts --seed and --config."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--seed", type=int, default=None, help="global seed (default: config.yaml)")
    p.add_argument("--config", type=Path, default=CONFIG_PATH)
    p.add_argument("--out", type=Path, default=OUTPUT_DIR)
    return p


def stage_seed(cfg: dict, stage: str, seed: int | None = None) -> int:
    """Derived seed for one stage: global seed + stage offset."""
    base = cfg["seed"] if seed is None else seed
    return base + cfg["stage_seed_offsets"][stage]


def stage_rng(cfg: dict, stage: str, seed: int | None = None) -> random.Random:
    return random.Random(stage_seed(cfg, stage, seed))


def sub_rng(rng: random.Random, label: int) -> random.Random:
    """Independent child stream, so adding draws in one sub-step does not shift another.

    `label` must be an int (str hashing is randomised per process).
    """
    return random.Random(rng.getrandbits(64) ^ label)


def make_faker(cfg: dict, seed: int, language: str | None = None) -> Faker:
    lang = language or cfg["languages"]["primary"]
    fk = Faker(cfg["faker_locales"][lang])
    fk.seed_instance(seed)
    return fk


def languages(cfg: dict) -> list[str]:
    """Active languages: primary plus secondary when configured."""
    langs = [cfg["languages"]["primary"]]
    if cfg["languages"].get("secondary_language"):
        langs.append(cfg["languages"]["secondary_language"])
    return langs


# ------------------------------------------------------------------ dates
def to_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def reference_date(cfg: dict) -> date:
    return to_date(cfg["reference_date"])


def cutover_date(cfg: dict) -> date:
    return to_date(cfg["cutover_date"])


def random_date(rng: random.Random, start: str | date, end: str | date) -> date:
    s, e = to_date(start), to_date(end)
    return s + timedelta(days=rng.randint(0, (e - s).days))


def iso(d: date) -> str:
    return d.isoformat()


# ------------------------------------------------------------------ geo
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def destination_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Point reached travelling distance_m from (lat, lon) on an initial bearing."""
    d = distance_m / EARTH_RADIUS_M
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def edge_distance_m(lat: float, lon: float, area: dict) -> float:
    """Distance from a point to a protected area's edge (negative = inside)."""
    return haversine_m(lat, lon, float(area["centre_latitude"]), float(area["centre_longitude"])) - float(area["radius_m"])


def point_at_edge_distance(area: dict, bearing_deg: float, edge_dist_m: float) -> tuple[float, float]:
    """Place a point exactly edge_dist_m outside the area's edge on a bearing from its centre."""
    return destination_point(
        float(area["centre_latitude"]), float(area["centre_longitude"]),
        bearing_deg, float(area["radius_m"]) + edge_dist_m,
    )


def round_coord(x: float) -> float:
    """DECIMAL(9,6)."""
    return round(x, 6)


def in_bbox(cfg: dict, lat: float, lon: float) -> bool:
    bb = cfg["bounding_box"]
    return bb["lat_min"] <= lat <= bb["lat_max"] and bb["lon_min"] <= lon <= bb["lon_max"]


# ------------------------------------------------------------------ ids
def seq_id(prefix: str, n: int, width: int = 4) -> str:
    """APP-0001, SITE-0001, DEC-0001 ..."""
    return f"{prefix}-{n:0{width}d}"


def random_digits_id(rng: random.Random, prefix: str, digits: int, taken: set[str] | None = None) -> str:
    """Invented formats: NID-XXXXXXXX, CR-XXXXXX, TL-XXXXXX, PLT-XXXXX ..."""
    while True:
        value = f"{prefix}-{rng.randrange(10 ** digits):0{digits}d}"
        if taken is None or value not in taken:
            if taken is not None:
                taken.add(value)
            return value


# ------------------------------------------------------------------ io
def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        fh.write("\n")


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def update_seeds_manifest(out_dir: Path, sections: dict[str, Any]) -> Path:
    """Replace some sections of <out>/seeds_manifest.json, keeping the others."""
    path = Path(out_dir) / SEEDS_MANIFEST.name
    manifest = read_json(path) if path.exists() else {}
    manifest.update(sections)
    write_json(path, manifest)
    return path


# ------------------------------------------------------------------ names (A-27)
def normalise_name(name: str, cfg: dict) -> str:
    """Lower-case, unify legal suffixes, drop punctuation, collapse whitespace."""
    aliases = cfg["name_matching"]["legal_suffix_aliases"]
    lookup = {form: canon for canon, forms in aliases.items() for form in forms}
    text = unicodedata.normalize("NFKC", name).lower().replace(",", " ")
    out = []
    for tok in text.split():
        tok = lookup.get(tok, tok)
        tok = re.sub(r"[^\w]", "", tok)
        if tok:
            out.append(tok)
    return " ".join(out)


def contains(items: Iterable[Any], obj: Any) -> bool:
    """Identity membership (records are dicts that reference each other)."""
    return any(x is obj for x in items)
