#!/usr/bin/env python
"""Stage 4 checks: rule corpus (YAML sources, PDFs, DOCX, chunks.jsonl, config_seed.sql).

Exit code 1 if any check fails.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import docx
import pymupdf
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generators"))
import common as C  # noqa: E402
from check_registry import Report  # noqa: E402

SPEC_CLAUSES = {("RD-01", "v1"): 10, ("RD-01", "v2"): 10, ("RD-02", "v1"): 7, ("RD-03", "v1"): 6,
                ("RD-04", "v1"): 8, ("RD-05", "v1"): 6, ("RD-06", "v1"): 5, ("RD-07", "v1"): 8}
FORBIDDEN = {  # corpus gaps (spec §2.3): no clause may address these
    "noise": r"\bnois", "desalination brine": r"desalinat|\bbrine", "data-centre cooling": r"data[- ]?cent|cooling",
    "temporary activities": r"temporar", "solar farms": r"\bsolar|photovoltaic",
}
CHUNK_FIELDS = ["id", "doc_id", "doc_title", "article", "clause_id", "clause_ref", "version", "effective_from",
                "effective_to", "status", "language", "permit_types", "text", "source_path", "page"]


def in_force(ch: dict, d: date) -> bool:
    """A-12: effective_from <= d and (effective_to is null or effective_to > d)."""
    return C.to_date(ch["effective_from"]) <= d and (ch["effective_to"] is None or C.to_date(ch["effective_to"]) > d)


def main():
    args = C.base_arg_parser("Stage 4 rule corpus checks").parse_args()
    cfg = C.load_config(args.config)
    out = args.out / "rules"
    rep = Report()
    chunks = [json.loads(line) for line in (out / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    srcs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((C.DATA_DIR / "rules_src").glob("RD-*.yaml"))]
    by_ref = {c["clause_ref"]: c for c in chunks if c["language"] == "en"}

    # ---------------------------------------------------------- sizes
    docs = {c["doc_id"] for c in chunks}
    versions = {(c["doc_id"], c["version"]) for c in chunks}
    rep.add("documents (>= 6)", ">=6", len(docs), len(docs) >= cfg["rules"]["min_documents"])
    rep.add("document versions", 8, len(versions))
    rep.add("clause-versions (>= 40)", ">=40", len(by_ref), len(by_ref) >= cfg["rules"]["min_clauses"])
    rep.add("unique clause IDs", 50, len({c["clause_id"] for c in chunks}))
    rep.add("clauses per document version (spec §2.1)", SPEC_CLAUSES,
            dict(Counter((c["doc_id"], c["version"]) for c in chunks if c["language"] == "en")))
    rep.add("YAML sources == chunks (en)", sum(len(s["clauses"]) for s in srcs), len(by_ref))

    # ---------------------------------------------------------- chunk records
    rep.add("chunk ids unique", True, len({c["id"] for c in chunks}) == len(chunks))
    rep.add("chunk ids valid AI Search keys", True, all(re.fullmatch(r"[A-Za-z0-9_\-=]+", c["id"]) for c in chunks))
    rep.add("chunk fields present (arch §9.1)", [], [f for f in CHUNK_FIELDS if any(f not in c for c in chunks)])
    rep.add("language field 'en' on every chunk", {"en"}, {c["language"] for c in chunks})
    rep.add("clause_ref == clause_id + version", True, all(c["clause_ref"] == f"{c['clause_id']} {c['version']}" for c in chunks))
    rep.add("permit_types valid", True, all(c["permit_types"] and set(c["permit_types"]) <= {"PT-1", "PT-2"} for c in chunks))
    rep.add("status values", {"current", "superseded"}, {c["status"] for c in chunks})

    # ---------------------------------------------------------- versions and in-force
    v1 = [c for c in chunks if c["doc_id"] == "RD-01" and c["version"] == "v1"]
    v2 = [c for c in chunks if c["doc_id"] == "RD-01" and c["version"] == "v2"]
    cut = C.cutover_date(cfg)
    rep.add("RD-01 v1 effective", ("2023-01-01", "2026-07-01", "superseded"),
            tuple({(c["effective_from"], c["effective_to"], c["status"]) for c in v1})[0])
    rep.add("RD-01 v2 effective", (cfg["cutover_date"], None, "current"),
            tuple({(c["effective_from"], c["effective_to"], c["status"]) for c in v2})[0])
    gaps, overlaps = [], []
    d = date(2023, 1, 1)
    while d <= date(2027, 12, 31):
        for cid in {c["clause_id"] for c in v1 + v2}:
            n = sum(in_force(c, d) for c in v1 + v2 if c["clause_id"] == cid)
            (gaps if n == 0 else overlaps if n > 1 else []).append(f"{cid}@{d}")
        d += timedelta(days=1)
    rep.add("RD-01: exactly one version in force every day 2023-2027", ([], []), (gaps[:2], overlaps[:2]))
    last_v1 = cut - timedelta(days=1)
    rep.add("RD-01 Art.6 in force on 2026-06-30 / 07-01", ("v1", "v2"),
            (next(c["version"] for c in v1 + v2 if c["clause_id"] == "RD-01 Art.6" and in_force(c, last_v1)),
             next(c["version"] for c in v1 + v2 if c["clause_id"] == "RD-01 Art.6" and in_force(c, cut))))
    others_ok = all(c["effective_to"] is None and c["status"] == "current" for c in chunks if c["doc_id"] != "RD-01")
    rep.add("RD-02..07 current, open-ended", True, others_ok)

    # ---------------------------------------------------------- seeded rules (spec §2.3)
    r = cfg["rules"]

    def has(ref, *needles):
        t = by_ref.get(ref, {}).get("text", "")
        return all(n in t for n in needles)

    rep.add("RD-01 Art.6 v1 = 300 m", True, has("RD-01 Art.6 v1", f"{r['buffer_m']['v1']} m"))
    rep.add("RD-01 Art.6 v2 = 500 m", True, has("RD-01 Art.6 v2", f"{r['buffer_m']['v2']} m"))
    rep.add("RD-01 Art.4: 90 days only in v2", (False, True),
            ("90 days" in by_ref["RD-01 Art.4 v1"]["text"], has("RD-01 Art.4 v2", "90 days", "application date")))
    rep.add("RD-05 Art.2 = 24 months", True, has("RD-05 Art.2 v1", "24 months"))
    rep.add("RD-01 Art.9 v2 = 12 months coastal, refers to RD-05", True,
            has("RD-01 Art.9 v2", "12 months", "coastal", "RD-05 Art. 2"))
    rep.add("RD-01 Art.9 v1 has no 12-month rule", False, "12 months" in by_ref["RD-01 Art.9 v1"]["text"])
    rep.add("RD-02 Art.3 PM10 cap", True, has("RD-02 Art.3 v1", "PM10", f"{r['pm10_cap_t_per_year']} t/yr"))
    rep.add("RD-03 Art.3 waste storage days", True, has("RD-03 Art.3 v1", f"{r['waste_storage_max_days']} days"))
    rep.add("RD-04 Art.3 abstraction 50,000 m³", True, has("RD-04 Art.3 v1", f"{r['abstraction_cap_m3_per_year']:,} m³"))
    vague = by_ref["RD-03 Art.5 v1"]["text"]
    rep.add("RD-03 Art.5 vague (no number)", True, "adequate secondary containment" in vague and not re.search(r"\d", vague))
    rep.add("no other clause defines containment", [],
            [c["clause_ref"] for c in chunks if "containment" in c["text"] and c["clause_ref"] not in
             ("RD-03 Art.5 v1", "RD-01 Art.7 v1", "RD-01 Art.7 v2")])
    hits = {topic: [c["clause_ref"] for c in chunks if re.search(rx, c["text"] + " " + c["title"], re.I)]
            for topic, rx in FORBIDDEN.items()}
    rep.add("corpus gaps: no clause on the 5 topics", {}, {k: v for k, v in hits.items() if v})

    # every threshold used by pack seeds exists as a clause parameter AND literally in its text
    needed = {
        "buffer_m (v1)": ("RD-01 Art.6 v1", "buffer_m", r["buffer_m"]["v1"]),
        "buffer_m (v2)": ("RD-01 Art.6 v2", "buffer_m", r["buffer_m"]["v2"]),
        "licence_min_remaining_days": ("RD-01 Art.4 v2", "licence_min_remaining_days", r["licence_min_remaining_days"]),
        "survey_max_age_months": ("RD-05 Art.2 v1", "survey_max_age_months", r["survey_max_age_months"]),
        "survey_max_age_months_coastal": ("RD-01 Art.9 v2", "survey_max_age_months_coastal", r["survey_max_age_months_coastal"]),
        "pm10_cap_t_per_year": ("RD-02 Art.3 v1", "pm10_cap_t_per_year", r["pm10_cap_t_per_year"]),
        "waste_storage_max_days": ("RD-03 Art.3 v1", "waste_storage_max_days", r["waste_storage_max_days"]),
        "abstraction_cap_m3_per_year": ("RD-04 Art.3 v1", "abstraction_cap_m3_per_year", r["abstraction_cap_m3_per_year"]),
        "pumping_test_above_m3_per_year": ("RD-05 Art.5 v1", "pumping_test_above_m3_per_year", r["pumping_test_above_m3_per_year"]),
        "coordinate_tolerance_m": ("RD-07 Art.5 v1", "coordinate_tolerance_m", cfg["thresholds"]["coordinate_tolerance_m"]),
        "rfi_response_days": ("RD-07 Art.8 v1", "rfi_response_days", r["rfi_response_days"]),
    }
    bad = []
    for name, (ref, key, val) in needed.items():
        ch = by_ref.get(ref)
        lit = f"{val:,}" if isinstance(val, int) and val >= 10000 else str(val)
        if not ch or ch["params"].get(key) != val or lit not in ch["text"]:
            bad.append(name)
    rep.add("every pack threshold in a clause (param + text)", [], bad)

    # clause refs used elsewhere exist
    refs = r["clause_refs"]
    flat = {v for x in refs.values() for v in (x.values() if isinstance(x, dict) else [x])}
    rep.add("config.clause_refs all exist", [], sorted(flat - set(by_ref)))
    dec_csv = args.out / "registry" / "prior_decisions.csv"
    if dec_csv.exists():
        import pandas as pd
        cited = {c for x in pd.read_csv(dec_csv)["clauses_cited"] for c in json.loads(x)}
        rep.add("prior_decisions clauses_cited all exist", [], sorted(cited - set(by_ref)))
        rep.add("decision cites in force on decision date", [], [
            f"{row.decision_id}:{c}" for row in pd.read_csv(dec_csv).itertuples()
            for c in json.loads(row.clauses_cited) if not in_force(by_ref[c], C.to_date(row.decision_date))][:3])

    # ---------------------------------------------------------- RD-07 Art.2 == checklist
    req = by_ref["RD-07 Art.2 v1"]["params"]["required_documents"]
    want = {pt: cfg["permit_types"][pt]["documents"] for pt in cfg["permit_types"]}
    rep.add("RD-07 Art.2 == config.yaml permit documents", want, req)
    sql = (out / "config_seed.sql").read_text(encoding="utf-8")
    items = re.findall(r"INSERT INTO config\.checklist_items .*? VALUES \(N'(PT-\d)', N'([\w-]+)'", sql)
    from_sql = {pt: [c for p, c in items if p == pt] for pt in want}
    rep.add("config_seed checklist_items == RD-07 Art.2", req, from_sql)
    rep.add("RD-07 Art.2 text names every document code", True,
            all(f"({code})" in by_ref["RD-07 Art.2 v1"]["text"] for codes in req.values() for code in codes))
    rep.add("config_seed row counts (items/factors/tiers/thresholds)",
            (11, len(cfg["risk"]["factors"]), 3, 14),
            tuple(len(re.findall(rf"INSERT INTO config\.{t} ", sql))
                  for t in ("checklist_items", "risk_factors", "risk_tiers", "thresholds")))
    rep.add("config_seed thresholds include A-20 0.80 and 50 m", True,
            "N'low_conf_field', 0.8," in sql and "N'coord_tolerance_m', 50," in sql)
    vr_json = [json.loads(m.replace("''", "'")) for m in re.findall(r", N'(\{.*?\})'\);", sql)]
    rep.add("checklist validation_rules valid JSON", 11, len(vr_json))

    # ---------------------------------------------------------- rendered files
    pdf_dir, docx_dir = out / "pdf", out / "docx"
    stems = sorted(f"{d}_{v}" for d, v in versions)
    rep.add("PDF per document version", stems, sorted(p.stem for p in pdf_dir.glob("*.pdf")))
    rep.add("DOCX per document version", stems, sorted(p.stem for p in docx_dir.glob("*.docx")))
    footer = cfg["packs"]["footer"]
    no_footer, page_bad = [], []
    for stem in stems:
        with pymupdf.open(pdf_dir / f"{stem}.pdf") as pdf:
            texts = [" ".join(p.get_text().split()) for p in pdf]
        no_footer += [f"{stem} p{i + 1}" for i, t in enumerate(texts) if footer not in t]
        for c in (c for c in chunks if f"{c['doc_id']}_{c['version']}" == stem):
            if c["text"][:60] not in " ".join(texts[c["page"] - 1:c["page"] + 1]):
                page_bad.append(c["id"])
        dx = docx.Document(docx_dir / f"{stem}.docx")
        if footer not in dx.sections[0].footer.paragraphs[0].text:
            no_footer.append(f"{stem}.docx")
        body = " ".join(p.text for p in dx.paragraphs)
        page_bad += [c["id"] for c in chunks if f"{c['doc_id']}_{c['version']}" == stem and c["text"] not in body]
    rep.add("footer on every PDF page and DOCX", [], no_footer[:3])
    rep.add("chunk text found in PDF (at its page) and DOCX", [], page_bad[:3])
    rep.add("source_path matches PDF name", True,
            all(c["source_path"] == f"Rules/{c['doc_id']}_{c['version']}.pdf" for c in chunks))

    # ---------------------------------------------------------- manifest
    man = json.loads((args.out / "seeds_manifest.json").read_text())
    rules_m = man.get("rules", {})
    rep.add("manifest: seeded rule cases", 8, len(rules_m.get("seeded_rules", [])))
    rep.add("manifest: seeded rule refs exist", [], sorted({x for s in rules_m.get("seeded_rules", [])
                                                         for x in s["clause_refs"]} - set(by_ref)))
    rep.add("manifest: docs pending secondary language", ["RD-01 v1", "RD-01 v2", "RD-04 v1", "RD-07 v1"],
            rules_m.get("pending_secondary_language_docs"))

    failed = rep.print()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
