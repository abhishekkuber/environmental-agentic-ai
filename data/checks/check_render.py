#!/usr/bin/env python
"""Stage 7 checks: rendered pack PDFs against facts.json.

Opens every PDF with pymupdf (independent of the renderer). Exit code 1 if any check fails.
"""
from __future__ import annotations

import hashlib
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generators"))
import common as C  # noqa: E402
from check_registry import Report  # noqa: E402


def coord(x):
    return f"{x:.6f}"


def num(x):
    return f"{int(x):,}"


def printed_values(code: str, f: dict) -> list[str]:
    """Key label fields (applicant, site, coordinates, quantities, dates) as they must appear in a digital PDF."""
    if code == "C1":
        v = [f["applicant_name"], f["applicant_id"], f["licence_number"], f["plot_id"], coord(f["site_lat"]),
             coord(f["site_lon"]), f["submission_date"], f["activity_description"]]
        dcl = f["declared"]
        if "pm10_t_per_year" in dcl:
            v += [str(dcl["pm10_t_per_year"]), num(dcl["waste_t_per_year"]), str(dcl["waste_storage_days"])]
        else:
            v += [num(dcl["abstraction_m3_per_year"])]
        return v
    if code == "C2":
        return [f["plot_id"], coord(f["site_lat"]), coord(f["site_lon"]), f["scale"], f["drawing_date"]]
    if code == "C3":
        return [f["licence_number"], f["holder_name"], f["issue_date"], f["expiry_date"]]
    if code == "C4":
        return [f["owner_name"], f["plot_id"], f["document_date"]] + ([f["authorised_party"]] if f["authorisation_present"] else [])
    if code == "P1-1":
        return [str(f["pm10_t_per_year"]), num(f["waste_t_per_year"]), f"{f['waste_storage_days']} days"]
    if code == "P2-1":
        return [coord(f["well_lat"]), coord(f["well_lon"]), num(f["abstraction_m3_per_year"]), f"{f['grout_depth_m']} m",
                f["driller_registration"]]
    if code == "P2-2":
        return [f["survey_date"], f["aquifer"], f["surveyor"], f["surveyor_registration"]]
    return []


def sharpness(pdf: pymupdf.Document) -> float:
    """Variance of the Laplacian of page 1 at 100 dpi (higher = sharper)."""
    pix = pdf[0].get_pixmap(dpi=100, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def main():
    args = C.base_arg_parser("Stage 7 render checks").parse_args()
    cfg = C.load_config(args.config)
    rep = Report()
    footer = cfg["packs"]["footer"]
    packs_root = args.out / "packs"
    pack_ids = sorted(d.name for d in packs_root.iterdir() if d.is_dir())

    missing, extra, absent_rendered, pages_bad, sha_bad, payload_bad = [], [], [], [], [], []
    no_footer, scan_text, digital_notext, meta_bad, printed_bad = [], [], [], [], []
    sig_bad, pf_totals, modes = [], {}, Counter()
    sharp = {"scan": [], "low_quality_scan": []}
    for pid in pack_ids:
        pd_ = packs_root / pid
        facts = C.read_json(pd_ / "facts.json")
        rman = C.read_json(pd_ / "render_manifest.json")
        rdocs = {d["code"]: d for d in rman["documents"]}
        files = {p.name for p in pd_.glob("*.pdf")}
        want = {d["filename"] for d in facts["documents"] if d["present"]}
        missing += [f"{pid}/{x}" for x in want - files]
        extra += [f"{pid}/{x}" for x in files - want]
        absent_rendered += [f"{pid}/{d['code']}" for d in facts["documents"] if not d["present"] and d["filename"] in files]
        if [d["filename"] for d in facts["portal_payload"]["documents"]] != [d["filename"] for d in facts["documents"] if d["present"]]:
            payload_bad.append(pid)
        total = 0
        for d in facts["documents"]:
            if not d["present"] or d["filename"] not in files:
                continue
            path = pd_ / d["filename"]
            rd, mode = d["render"], d["render"]["mode"]
            modes[mode] += 1
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != rdocs[d["code"]]["sha256"]:
                sha_bad.append(f"{pid}/{d['code']}")
            with pymupdf.open(stream=data, filetype="pdf") as pdf:
                expected = rd["pages"] + rd.get("annex_pages", 0)
                total += pdf.page_count
                if pdf.page_count != expected:
                    pages_bad.append(f"{pid}/{d['code']}:{pdf.page_count}!={expected}")
                md = pdf.metadata
                if md["subject"] != footer or not md["creationDate"].startswith("D:" + cfg["reference_date"].replace("-", "")):
                    meta_bad.append(f"{pid}/{d['code']}")
                texts = [" ".join(p.get_text().split()) for p in pdf]
                if mode == "digital":
                    if any(footer not in t for t in texts):
                        no_footer.append(f"{pid}/{d['code']}")
                    if any(not t for t in texts):
                        digital_notext.append(f"{pid}/{d['code']}")
                    alltext = " ".join(texts)
                    printed_bad += [f"{pid}/{d['code']}:{v}" for v in printed_values(d["code"], d["fields"]) if v not in alltext]
                else:
                    if any(t for t in texts) or any(not p.get_images() for p in pdf):
                        scan_text.append(f"{pid}/{d['code']}")
                    if not rdocs[d["code"]]["footer_verified_before_scan"]:
                        no_footer.append(f"{pid}/{d['code']}(scan)")
                    sharp[mode].append(sharpness(pdf))
            if d["code"] == "C1":
                if rdocs["C1"]["signature_image_drawn"] != d["fields"]["signature_present"]:
                    sig_bad.append(pid)
        if facts["meta"]["set"] == "performance":
            pf_totals[pid] = total

    rep.add("PDF for every present document", [], missing[:3])
    rep.add("no unexpected PDFs", [], extra[:3])
    rep.add("missing documents not rendered", [], absent_rendered[:3])
    rep.add("portal payload filenames == rendered files", [], payload_bad)
    rep.add("page count == planned (+ annex)", [], pages_bad[:3])
    rep.add("performance packs total 30 pages", {p: 30 for p in pf_totals}, pf_totals)
    rep.add("render_manifest sha256 matches files", [], sha_bad[:3])
    rep.add("footer on every page (digital text / scans pre-check)", [], no_footer[:3])
    rep.add("metadata: footer subject + fixed date on every PDF", [], meta_bad[:3])
    rep.add("digital PDFs have a text layer on every page", [], digital_notext[:3])
    rep.add("scans are image-only (no text layer)", [], scan_text[:3])
    share = (modes["scan"] + modes["low_quality_scan"]) / sum(modes.values())
    rep.add("scan share ~30%", "0.25-0.35", round(share, 3), 0.25 <= share <= 0.35)
    rep.add("low-quality scans (seeded)", 2, modes["low_quality_scan"])
    lq_max = max(sharp["low_quality_scan"]) if sharp["low_quality_scan"] else 0
    sc_min = min(sharp["scan"]) if sharp["scan"] else 0
    rep.add("low-quality scans blurrier than every normal scan", f"< {sc_min:.0f}", round(lq_max, 1), lq_max < 0.5 * sc_min)
    rep.add("key field values printed in digital PDFs", [], printed_bad[:3])
    rep.add("signature drawn iff signature_present (C1)", [], sig_bad)

    # injections
    hostile = next(pid for pid in pack_ids if "hostile_instructions" in C.read_json(packs_root / pid / "facts.json")["seeds"])
    with pymupdf.open(packs_root / hostile / "emissions_waste_summary.pdf") as pdf:
        on_p2 = cfg["packs"]["injection_text"] in " ".join(pdf[1].get_text().split())
        elsewhere = any(cfg["packs"]["injection_text"] in " ".join(pdf[i].get_text().split()) for i in range(pdf.page_count) if i != 1)
    rep.add(f"hostile text on page 2 of {hostile} P1-1 only", (True, False), (on_p2, elsewhere))
    rt02 = cfg["pack_facts"]["injections"]["R-02"]
    with pymupdf.open(packs_root / "RT-02" / "emissions_waste_summary.pdf") as pdf:
        spans = [s for b in pdf[rt02["page"] - 1].get_text("dict")["blocks"] for line in b.get("lines", [])
                 for s in line["spans"] if "AUTOMATED REVIEWER" in s["text"]]
    rep.add("RT-02 hidden text: extractable, white, 2 pt", True,
            bool(spans) and all(s["color"] == 0xFFFFFF and abs(s["size"] - 2) < 0.3 for s in spans))
    inj_bad = []
    for rid, spec in cfg["pack_facts"]["injections"].items():
        if rid in ("R-01", "R-02"):
            continue
        pid = f"RT-{rid[2:]}"
        f = C.read_json(packs_root / pid / "facts.json")
        fn = next(d["filename"] for d in f["documents"] if d["code"] == spec["doc"])
        with pymupdf.open(packs_root / pid / fn) as pdf:
            if spec["text"][:40] not in " ".join(pdf[spec["page"] - 1].get_text().split()):
                inj_bad.append(pid)
    rep.add("red-team injection text on its planned page", [], inj_bad)

    failed = rep.print()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
