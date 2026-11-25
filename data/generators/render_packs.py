#!/usr/bin/env python
"""Stage 7: render pack PDFs from facts.json (decides nothing itself).

For each pack in <out>/packs/{pack_id}/facts.json:
  - every present document -> HTML (render/templates/{lang}/) -> PDF (WeasyPrint)
    with exactly the planned number of pages and the footer on every page;
  - render.mode "scan" / "low_quality_scan" -> rasterise, rotate +-2 deg, blur,
    noise, JPEG -> image-only PDF (no text layer);
  - injections are placed on the planned page (visible box, or white 2 pt text);
  - performance packs get annex pages so the pack totals 30 pages;
  - writes <out>/packs/{pack_id}/render_manifest.json.
Deterministic: fixed metadata dates, per-document noise seeds, no new PDF IDs.
Exercise content: fictional data.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent / "render" / "templates"
SECTIONS = {
    "C1": ["C1_details", "C1_site", "C1_declaration"],
    "C2": ["C2_plan", "C2_notes"],
    "C3": ["C3_certificate"],
    "C4": ["C4_deed", "C4_schedule"],
    "P1-1": ["P11_summary", "P11_waste", "P11_controls", "P11_notes"],
    "P2-1": ["P21_design", "P21_construction", "P21_section"],
    "P2-2": ["P22_summary", "P22_aquifer", "P22_quality", "P22_pumping", "P22_method", "P22_signoff"],
}
TITLES = {
    "C2": "Site plan", "C3": "Trade licence", "C4": "Evidence of land ownership",
    "P1-1": "Emissions and waste summary", "P2-1": "Well design statement", "P2-2": "Hydrogeological survey report",
}
PERMIT_LONG = {"PT-1": "Environmental No-Objection Certificate (NOC)", "PT-2": "Groundwater well permit"}
SCAN = {"scan": dict(dpi=150, blur=3, noise=6.0, quality=60),
        "low_quality_scan": dict(dpi=70, blur=9, noise=14.0, quality=25)}

_ENV: dict[str, Environment] = {}


def env(lang: str) -> Environment:
    if lang not in _ENV:
        _ENV[lang] = Environment(loader=FileSystemLoader(TEMPLATES / lang), undefined=StrictUndefined, autoescape=True)
    return _ENV[lang]


def doc_seed(seed: int, pack_id: str, code: str) -> int:
    return int(hashlib.sha256(f"{seed}:{pack_id}:{code}".encode()).hexdigest()[:8], 16)


def fmt_coord(x: float) -> str:
    return f"{x:.6f}"


def fmt_int(x) -> str:
    return f"{int(x):,}"


def pdf_date(iso: str) -> str:
    return "D:" + iso.replace("-", "") + "000000"


# ------------------------------------------------------------------ assets
def make_signature() -> str:
    """Invented scribble + generic round stamp (no real authority's seal), as a data URI.

    Formula-based and embedded inline, so output does not depend on file paths.
    """
    img = Image.new("RGB", (600, 220), "white")
    d = ImageDraw.Draw(img)
    pts = [(40 + i * 3, 110 + 38 * math.sin(i / 9.0) * math.cos(i / 23.0)) for i in range(120)]
    d.line(pts, fill=(20, 30, 110), width=5)
    d.ellipse((420, 30, 580, 190), outline=(150, 20, 30), width=6)
    d.ellipse((440, 50, 560, 170), outline=(150, 20, 30), width=2)
    d.text((462, 95), "SIGNED", fill=(150, 20, 30))
    d.text((452, 115), "exercise", fill=(150, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ layout
def split_sections(keys: list[str], n_pages: int) -> list[list[str]]:
    """Contiguous, as-even-as-possible groups; one group per page."""
    n = len(keys)
    if n_pages > n:
        raise ValueError(f"{n_pages} pages planned but only {n} sections")
    base, extra = divmod(n, n_pages)
    out, i = [], 0
    for p in range(n_pages):
        size = base + (1 if p < extra else 0)
        out.append(keys[i:i + size])
        i += size
    return out


def annex_pages(facts: dict, n: int) -> list[dict]:
    """Supporting-record tables for performance packs, derived from the facts by formula (not label fields)."""
    dec = next(d for d in facts["documents"] if d["code"] == "C1")["fields"]["declared"]
    base = dec.get("pm10_t_per_year") or dec["abstraction_m3_per_year"] / 1000
    pages = []
    for a in range(1, n + 1):
        rows = [(m, f"{base / 12 * (0.85 + 0.03 * ((m * 7 + a) % 10)):.2f}") for m in range(1, 13)]
        pages.append({"sections": ["annex"], "fields": {"no": a, "rows": rows}, "injection": None})
    return pages


def build_html(facts: dict, d: dict, cfg: dict, signature_uri: str) -> str:
    code, rd = d["code"], d["render"]
    groups = split_sections(SECTIONS[code], rd["pages"])
    pages = [{"sections": g, "fields": None, "injection": None} for g in groups]
    inj = rd.get("injection")
    if inj:
        pages[inj["page"] - 1]["injection"] = inj
    if rd.get("annex_pages"):
        pages += annex_pages(facts, rd["annex_pages"])
    pt = facts["permit_type"]
    art = "an" if PERMIT_LONG[pt][0] in "AEIOU" else "a"
    title = f"Application for {art} {PERMIT_LONG[pt]}" if code == "C1" else TITLES[code]
    ctx = {"permit_type": pt, "permit_name": PERMIT_LONG[pt], "contact_email": facts["applicant"]["contact_email"],
           "applicant_name": facts["applicant"]["name"], "reference": facts["submission_id"],
           "site_address": facts["site"]["address"], "signature_img": signature_uri,
           "fmt_coord": fmt_coord, "fmt_int": fmt_int}
    keywords = f"pack {facts['pack_id']}; language {d['language']}"
    if d.get("target_language") or facts.get("target_language"):
        keywords += f"; target_language {d.get('target_language') or facts['target_language']} (pending)"
    return env(d["language"]).get_template("document.html").render(
        lang=d["language"], title=title, subtitle=f"{PERMIT_LONG[pt]}: {facts['applicant']['name']}",
        header_left=facts["applicant"]["name"], reference=facts["submission_id"], footer=cfg["packs"]["footer"],
        keywords=keywords, build_date=cfg["reference_date"], pages=pages, f=d["fields"], ctx=ctx)


# ------------------------------------------------------------------ PDF steps
def html_to_pdf(html: str) -> bytes:
    from weasyprint import HTML  # imported in the worker
    return HTML(string=html, base_url=str(TEMPLATES)).write_pdf()


def verify_digital(pdf: bytes, expected_pages: int, footer: str) -> None:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        if doc.page_count != expected_pages:
            raise RuntimeError(f"rendered {doc.page_count} pages, planned {expected_pages}")
        for i, page in enumerate(doc):
            if footer not in " ".join(page.get_text().split()):
                raise RuntimeError(f"footer missing on page {i + 1}")


def scan(pdf: bytes, mode: str, seed: int, meta: dict) -> bytes:
    p = SCAN[mode]
    rng = np.random.default_rng(seed)
    src = pymupdf.open(stream=pdf, filetype="pdf")
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=p["dpi"], colorspace=pymupdf.csGRAY)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
        h, w = img.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), float(rng.uniform(-2.0, 2.0)), 1.0)
        img = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
        img = cv2.GaussianBlur(img, (p["blur"], p["blur"]), 0)
        img = np.clip(img.astype(np.float32) + rng.normal(0, p["noise"], img.shape), 0, 255).astype(np.uint8)
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, p["quality"]])
        assert ok
        np_ = out.new_page(width=page.rect.width, height=page.rect.height)
        np_.insert_image(np_.rect, stream=jpg.tobytes())
    out.set_metadata(meta)
    data = out.tobytes(garbage=4, deflate=True, no_new_id=True)
    src.close()
    out.close()
    return data


def render_pack(args: tuple) -> dict:
    pack_dir, cfg, seed, signature_uri = args
    facts = C.read_json(pack_dir / "facts.json")
    for old in pack_dir.glob("*.pdf"):
        old.unlink()
    footer = cfg["packs"]["footer"]
    docs = []
    for d in facts["documents"]:
        if not d["present"]:
            continue
        rd = d["render"]
        expected = rd["pages"] + rd.get("annex_pages", 0)
        pdf = html_to_pdf(build_html(facts, d, cfg, signature_uri))
        verify_digital(pdf, expected, footer)  # also the footer check for scans, before rasterising
        with pymupdf.open(stream=pdf, filetype="pdf") as dd:  # recorded before a scan hides it
            images_last_page = len(dd[rd["pages"] - 1].get_images())
        if rd["mode"] in SCAN:
            meta = {"title": f"{d['filename']} (scanned copy)", "subject": footer, "author": "", "creator": "scanner",
                    "producer": "UC1 synthetic data", "keywords": f"pack {facts['pack_id']}",
                    "creationDate": pdf_date(cfg["reference_date"]), "modDate": pdf_date(cfg["reference_date"])}
            pdf = scan(pdf, rd["mode"], doc_seed(seed, facts["pack_id"], d["code"]), meta)
        (pack_dir / d["filename"]).write_bytes(pdf)
        docs.append({"code": d["code"], "filename": d["filename"], "mode": rd["mode"], "pages": expected,
                     "annex_pages": rd.get("annex_pages", 0), "footer_verified_before_scan": True,
                     "signature_image_drawn": images_last_page > 0 if d["code"] in ("C1", "P2-2") else None,
                     "injection_page": rd["injection"]["page"] if rd.get("injection") else None,
                     "sha256": hashlib.sha256(pdf).hexdigest()})
    manifest = {"pack_id": facts["pack_id"], "total_pages": sum(x["pages"] for x in docs), "documents": docs,
                "not_rendered": [d["code"] for d in facts["documents"] if not d["present"]]}
    C.write_json(pack_dir / "render_manifest.json", manifest)
    return manifest


def main():
    p = C.base_arg_parser(__doc__.splitlines()[0])
    p.add_argument("--packs", default="", help="comma-separated pack IDs (default: all)")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = p.parse_args()
    cfg = C.load_config(args.config)
    seed = C.stage_seed(cfg, "render", args.seed)
    sig = make_signature()
    packs_root = args.out / "packs"
    wanted = [x for x in args.packs.split(",") if x] or sorted(d.name for d in packs_root.iterdir() if d.is_dir())
    jobs = [(packs_root / pid, cfg, seed, sig) for pid in wanted]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(render_pack, jobs))
    n_docs = sum(len(r["documents"]) for r in results)
    modes = {}
    for r in results:
        for d in r["documents"]:
            modes[d["mode"]] = modes.get(d["mode"], 0) + 1
    print(f"packs {len(results)}, documents {n_docs}, pages {sum(r['total_pages'] for r in results)}, modes {modes}")


if __name__ == "__main__":
    main()
