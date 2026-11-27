#!/usr/bin/env python
"""Stage 9: one summary table, spec target vs actual, for every count and seed.

Counts are taken directly from the generated files (CSVs, chunks, labels, render manifests,
eval sets), not from generator code. Prints the table, writes it as Markdown to
<out>/summary.md, and exits 1 if any row misses its target. Runs last in `make check`.
Exercise content: fictional data.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generators"))
import common as C  # noqa: E402


def jl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class Table:
    def __init__(self):
        self.rows: list[tuple[str, str, str, str, bool]] = []

    def add(self, area: str, item: str, target, actual, ok: bool | None = None):
        ok = (target == actual) if ok is None else ok
        self.rows.append((area, item, str(target), str(actual), bool(ok)))

    def print(self) -> int:
        w = [max(len(r[i]) for r in self.rows) for i in range(4)]
        head = ("area", "item", "spec target", "actual")
        print("  ".join(h.ljust(w[i]) for i, h in enumerate(head)) + "  result")
        print("  ".join("-" * w[i] for i in range(4)) + "  ------")
        for r in self.rows:
            print("  ".join(r[i].ljust(w[i]) for i in range(4)) + ("  PASS" if r[4] else "  FAIL"))
        failed = sum(not r[4] for r in self.rows)
        print(f"\n{len(self.rows) - failed}/{len(self.rows)} match the spec")
        return failed

    def markdown(self) -> str:
        out = ["| Area | Item | Spec target | Actual | Result |", "|---|---|---|---|---|"]
        out += [f"| {a} | {i} | {t} | {v} | {'pass' if ok else '**FAIL**'} |" for a, i, t, v, ok in self.rows]
        return "\n".join(out) + "\n"


def main():
    args = C.base_arg_parser("Stage 9 summary: spec target vs actual").parse_args()
    cfg = C.load_config(args.config)
    out = args.out
    T = Table()
    man = C.read_json(out / "seeds_manifest.json")
    reg = out / "registry"
    csv = {n: pd.read_csv(reg / f"{n}.csv", dtype=str) for n in
           ("applicants", "sites", "protected_areas", "licences", "prior_decisions", "open_cases")}

    # ------------------------------------------------------------ registry (spec §1)
    a, s, lic, dec, oc = (csv[k] for k in ("applicants", "sites", "licences", "prior_decisions", "open_cases"))
    T.add("registry", "applicants", 300, len(a))
    refused = set(dec.loc[dec["outcome"] == "refused", "applicant_id"])
    enf = set(oc.loc[oc["case_type"] == "enforcement", "applicant_id"])
    T.add("registry", "applicants with >= 1 refusal", ">= 30", len(refused), len(refused) >= 30)
    T.add("registry", "applicants with open enforcement", 12, len(enf))
    T.add("registry", "nationalities", "~10", a["nationality"].nunique(), a["nationality"].nunique() == 10)
    T.add("registry", "preferred_language", "en only (secondary deferred)", sorted(a["preferred_language"].unique()),
          set(a["preferred_language"]) == {"en"})
    T.add("registry", "sites", 500, len(s))
    areas = [dict(r) for _, r in csv["protected_areas"].iterrows()]
    d = [min(C.edge_distance_m(float(r.latitude), float(r.longitude), x) for x in areas) for r in s.itertuples()]
    bands = Counter("<300" if x < 300 else "300-500" if x < 500 else "500-700" if x < 700 else ">700" for x in d)
    T.add("registry", "sites < 300 m / 300-500 m / 500-700 m / > 700 m", "25 / 15 / 20 / 440",
          f"{bands['<300']} / {bands['300-500']} / {bands['500-700']} / {bands['>700']}",
          (bands["<300"], bands["300-500"], bands["500-700"], bands[">700"]) == (25, 15, 20, 440))
    T.add("registry", "protected areas (5 types)", "12 (5)", f"{len(areas)} ({len({x['area_type'] for x in areas})})",
          len(areas) == 12 and len({x["area_type"] for x in areas}) == 5)
    lt = Counter(lic["licence_type"])
    T.add("registry", "licences (trade + prior permits)", "~540 (~420 + ~120)",
          f"{len(lic)} ({lt['trade']} + {lt['environmental_permit'] + lt['well_permit']})",
          abs(len(lic) - 540) <= 30)
    seeds = Counter(x["seed_type"] for x in man["registry_seeds"])
    bg = Counter(x["kind"] for x in man["background"])
    T.add("registry", "licences expired before application (linked + background)", "10",
          f"{seeds['licence_expired_before_application']} + {bg['licence_expired_unlinked']}",
          seeds["licence_expired_before_application"] + bg["licence_expired_unlinked"] == 10)
    T.add("registry", "licences expiring within 90 days (linked + background)", "6",
          f"{seeds['licence_expiring_within_90_days']} + {bg['licence_expiring_unlinked']}",
          seeds["licence_expiring_within_90_days"] + bg["licence_expiring_unlinked"] == 6)
    T.add("registry", "holder-name spelling variants", 3, seeds["licence_holder_name_variant"])
    T.add("registry", "licence numbers absent from registry", 2, seeds["licence_number_absent_from_registry"])
    oc_ = Counter(dec["outcome"])
    T.add("registry", "prior decisions", 150, len(dec))
    T.add("registry", "approved / with conditions / refused", "85 / 35 / 30 (>= 30 refusals wins over 15%)",
          f"{oc_['approved']} / {oc_['approved_with_conditions']} / {oc_['refused']}",
          (oc_["approved"], oc_["approved_with_conditions"], oc_["refused"]) == (85, 35, 30))
    T.add("registry", "decisions flagged for a precedent", 60, int((dec["has_precedent_summary"] == "1").sum()))
    ct = Counter(oc["case_type"])
    T.add("registry", "open cases (enf / complaint / pending / audit)", "40 (12/10/10/8)",
          f"{len(oc)} ({ct['enforcement']}/{ct['complaint']}/{ct['pending_application']}/{ct['compliance_audit']})",
          (len(oc), ct["enforcement"], ct["complaint"], ct["pending_application"], ct["compliance_audit"]) == (40, 12, 10, 10, 8))

    # ------------------------------------------------------------ rules (spec §2)
    ch = [c for c in jl(out / "rules" / "chunks.jsonl") if c["language"] == "en"]
    docs = {c["doc_id"] for c in ch}
    vers = {(c["doc_id"], c["version"]) for c in ch}
    T.add("rules", "documents / versions", ">= 6 / 8", f"{len(docs)} / {len(vers)}", len(docs) >= 6 and len(vers) == 8)
    T.add("rules", "clauses (clause-versions)", ">= 40", len(ch), len(ch) >= 40)
    T.add("rules", "RD-01 in two versions", "v1 + v2", sorted(v for dd, v in vers if dd == "RD-01"),
          sorted(v for dd, v in vers if dd == "RD-01") == ["v1", "v2"])
    T.add("rules", "seeded rule cases", 8, len(man["rules"]["seeded_rules"]))
    rd = out / "rules"
    T.add("rules", "Word + PDF files", "8 + 8", f"{len(list((rd / 'docx').glob('*.docx')))} + {len(list((rd / 'pdf').glob('*.pdf')))}",
          len(list((rd / "docx").glob("*.docx"))) == 8 and len(list((rd / "pdf").glob("*.pdf"))) == 8)

    # ------------------------------------------------------------ precedents (spec §3)
    pr = jl(out / "precedents" / "precedents.jsonl")
    roles = Counter(p["role"] for p in man["precedents"])
    T.add("precedents", "summaries", ">= 50 (60 planned)", len(pr), len(pr) == 60)
    T.add("precedents", "close / distant / decoy / generic", "20 / 20 / 10 / 10",
          f"{roles['close']} / {roles['distant']} / {roles['decoy']} / {roles['generic']}",
          (roles["close"], roles["distant"], roles["decoy"], roles["generic"]) == (20, 20, 10, 10))
    T.add("precedents", "cite superseded RD-01 v1", 5, sum(p["cites_superseded"] for p in pr))
    words = [len(p["summary"].split()) for p in pr]
    T.add("precedents", "summary length (words)", "150-250", f"{min(words)}-{max(words)}", 150 <= min(words) and max(words) <= 250)
    T.add("precedents", "gold top-3 packs", 20, len(jl(out / "eval" / "precedents_gold.jsonl")))

    # ------------------------------------------------------------ packs (spec §4)
    labels = {p.stem: C.read_json(p) for p in sorted((out / "labels").glob("*.json"))}
    sets = Counter(l["set"] for l in labels.values())
    T.add("packs", "total packs", 105, len(labels))
    T.add("packs", "core complete / seeded", "40 / 20", f"{sets['core_complete']} / {sets['core_seeded']}",
          (sets["core_complete"], sets["core_seeded"]) == (40, 20))
    grp = Counter(l["group"] for l in labels.values() if l["set"] == "core_complete")
    want = {"clean": 16, "buffer": 6, "qty_above": 6, "not_covered": 4, "prior_history": 4, "expiring_licence": 4}
    T.add("packs", "complete groups: clean/buffer/qty/not covered/history/expiring", "16/6/6/4/4/4",
          "/".join(str(grp[k]) for k in want), dict(grp) == want)
    sg = Counter(l["group"] for l in labels.values() if l["set"] == "core_seeded")
    want_s = {"missing_signature": 3, "expired_licence": 3, "expired_licence+missing_survey": 1, "missing_survey": 1,
              "mismatched_coordinates": 4, "inconsistent_quantities": 4, "low_quality_scan": 2,
              "secondary_language_only_document": 1, "hostile_instructions": 1}
    T.add("packs", "seeded groups (sig/lic/A2/survey/coords/qty/scan/lang/hostile)", "3/3/1/1/4/4/2/1/1",
          "/".join(str(sg[k]) for k in want_s), dict(sg) == want_s)
    pre = [k for k, l in labels.items() if l["set"] == "core_complete" and l["application_date"] < cfg["cutover_date"]]
    T.add("packs", "complete packs dated before the cutover", 6, len(pre))
    T.add("packs", "complete packs labelled complete", 40, sum(l["expected_verdict"] == "complete"
                                                               for l in labels.values() if l["set"] == "core_complete"))
    T.add("packs", "seeded packs incomplete (except hostile + language-only)", 18,
          sum(l["expected_verdict"] == "incomplete" for l in labels.values() if l["set"] == "core_seeded"))
    T.add("packs", "fairness / resubmission / red team / performance", "20 / 15 / 5 / 5",
          f"{sets['fairness']} / {sets['resubmission']} / {sets['red_team']} / {sets['performance']}",
          (sets["fairness"], sets["resubmission"], sets["red_team"], sets["performance"]) == (20, 15, 5, 5))
    rs = Counter(l["group"] for l in labels.values() if l["set"] == "resubmission")
    T.add("packs", "resubmissions fixed / still defective", "10 / 5", f"{rs['fixed']} / {rs['still_defective']}",
          (rs["fixed"], rs["still_defective"]) == (10, 5))
    T.add("packs", "resubmissions with parent_case_id", 15, sum(bool(l["parent_case_id"]) for l in labels.values()
                                                               if l["set"] == "resubmission"))
    inj = [k for k, l in labels.items() if l["injection_expected"]]
    T.add("packs", "injection packs routed to officer, never approved (A4)", 6,
          sum(labels[k]["expected_outcome"] == "routed_to_officer_injection_suspected" and not labels[k]["approval_allowed"]
              for k in inj))
    pend = sorted(k for k, l in labels.items() if l["pending_secondary_language"])
    T.add("packs", "packs flagged pending_secondary_language", "18 (13 core + RT-03 + 4 FV)", len(pend),
          len(pend) == 18 and set(pend) == set(man["pending_secondary_language"]))

    # ------------------------------------------------------------ rendering (spec §4.2)
    docs_r, pages, modes, pf_pages = 0, 0, Counter(), []
    for k in labels:
        rm = C.read_json(out / "packs" / k / "render_manifest.json")
        docs_r += len(rm["documents"])
        pg = sum(x["pages"] for x in rm["documents"])  # pages already include annex pages
        pages += pg
        modes.update(x["mode"] for x in rm["documents"])
        if k.startswith("PF-"):
            pf_pages.append(pg)
    pdfs = len(list((out / "packs").rglob("*.pdf")))
    T.add("rendering", "PDF documents (= render manifests)", docs_r, pdfs)
    T.add("rendering", "pages", "-", pages, True)
    scan = (modes["scan"] + modes["low_quality_scan"]) / docs_r
    T.add("rendering", "scanned share", "~30%", f"{scan:.1%}", 0.27 <= scan <= 0.33)
    T.add("rendering", "low-quality scans", 2, modes["low_quality_scan"])
    T.add("rendering", "performance packs at 30 pages", "5 x 30", " ".join(map(str, pf_pages)), pf_pages == [30] * 5)

    # ------------------------------------------------------------ eval (spec §7) + templates (§5)
    ev = out / "eval"
    rq = jl(ev / "retrieval.jsonl")
    sc = Counter(r["scope"] for r in rq)
    T.add("eval", "retrieval questions PT-1 / PT-2 / cross", "150 (60/60/30)", f"{len(rq)} ({sc['PT-1']}/{sc['PT-2']}/{sc['cross']})",
          (len(rq), sc["PT-1"], sc["PT-2"], sc["cross"]) == (150, 60, 60, 30))
    T.add("eval", "superseded-trap questions", 25, sum(bool(r["superseded_trap_ids"]) for r in rq))
    T.add("eval", "secondary-language retrieval questions", "40 (deferred)", "0 (reserved)", True)
    T.add("eval", "unanswerable questions", 30, len(jl(ev / "unanswerable.jsonl")))
    tj = yaml.safe_load((ev / "trajectories.yaml").read_text(encoding="utf-8"))["scenarios"]
    T.add("eval", "trajectory scenarios (A1-A8 + variants)", "30 (8 + 22)",
          f"{len(tj)} ({sum(t['id'].startswith('A') for t in tj)} + {sum(t['id'].startswith('V') for t in tj)})",
          len(tj) == 30 and sum(t["id"].startswith("A") for t in tj) == 8)
    rt = yaml.safe_load((ev / "redteam.yaml").read_text(encoding="utf-8"))["cases"]
    T.add("eval", "adversarial cases", ">= 20 (21 planned)", len(rt), len(rt) == 21)
    cs = yaml.safe_load((ev / "consistency.yaml").read_text(encoding="utf-8"))
    T.add("eval", "consistency packs x runs", "20 x 5", f"{len(cs['packs'])} x {cs['runs_per_pack']}",
          (len(cs["packs"]), cs["runs_per_pack"]) == (20, 5))
    T.add("eval", "fairness pairs", 20, len(jl(ev / "fairness_pairs.jsonl")))
    ex = out / "templates" / "examples"
    n_ex = len(list(ex.glob("*.pdf"))) if ex.exists() else 0
    T.add("templates", "decision pack + RFI letter (EN; secondary deferred)", "2 templates, 3 examples",
          f"{n_ex} examples (PDF + DOCX)", n_ex == 3)

    failed = T.print()
    (out / "summary.md").write_text(
        "# Spec target vs actual\n\n*Generated by data/checks/check_summary.py. Exercise content: fictional data.*\n\n"
        + T.markdown(), encoding="utf-8")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
