#!/usr/bin/env python
"""Stage 6 checks: pack facts and labels.

Re-measures every seed in the facts, re-derives completeness with its own small
checklist (does not import labeller.py), and checks group outcomes, clause
versions, fairness pairs and resubmissions. Exit code 1 if any check fails.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generators"))
import common as C  # noqa: E402
from check_registry import Report  # noqa: E402

DAY = timedelta(days=1)
LABEL_KEYS = ["pack_id", "permit_type", "application_date", "language", "documents", "completeness",
              "expected_verdict", "expected_rfi_items", "expected_findings", "expected_risk_tier", "seeds", "scenario"]
IDENTITY = {"pack_id", "case_id", "set", "documents", "officer_notes", "expected_rfi_items", "scenario", "seeds"}


def months(a: date, b: date) -> int:
    m = (b.year - a.year) * 12 + b.month - a.month
    return m - 1 if b.day < a.day else m


def main():
    args = C.base_arg_parser("Stage 6 pack checks").parse_args()
    cfg = C.load_config(args.config)
    rep = Report()
    out = args.out
    man = C.read_json(out / "seeds_manifest.json")
    plan = {p["pack_id"]: p for p in man["packs"]}
    facts = {p: C.read_json(out / "packs" / p / "facts.json") for p in plan}
    labels = {p: C.read_json(out / "labels" / f"{p}.json") for p in plan}
    lic = pd.read_csv(out / "registry" / "licences.csv", dtype=str).set_index("licence_number")
    apps = pd.read_csv(out / "registry" / "applicants.csv", dtype=str).set_index("applicant_id")
    sites = pd.read_csv(out / "registry" / "sites.csv", dtype=str).set_index("site_id")
    chunks = [json.loads(x) for x in (out / "rules" / "chunks.jsonl").read_text().splitlines()]
    by_ref = {c["clause_ref"]: c for c in chunks}
    cut, tol = C.cutover_date(cfg), cfg["thresholds"]["coordinate_tolerance_m"]
    R = cfg["rules"]

    def doc(f, code):
        return next((x for x in f["documents"] if x["code"] == code), None)

    def fld(f, code):
        x = doc(f, code)
        return x["fields"] if x and x["present"] else None

    def in_force(ref, d):
        c = by_ref[ref]
        return C.to_date(c["effective_from"]) <= d and (c["effective_to"] is None or C.to_date(c["effective_to"]) > d)

    # ---------------------------------------------------------- counts and format
    rep.add("facts.json per pack", len(plan), sum((out / "packs" / p / "facts.json").exists() for p in plan))
    rep.add("label per pack", len(plan), sum((out / "labels" / f"{p}.json").exists() for p in plan))
    rep.add("packs by set", {"core_complete": 40, "core_seeded": 20, "fairness": 20, "resubmission": 15,
                             "red_team": 5, "performance": 5}, dict(Counter(f["meta"]["set"] for f in facts.values())))
    rep.add("label has spec §6 keys", [], [p for p, l in labels.items() if any(k not in l for k in LABEL_KEYS)])
    rep.add("checklist items == config per permit type", [],
            [p for p, l in labels.items() if list(l["completeness"]) != cfg["permit_types"][l["permit_type"]]["documents"]])
    rep.add("footer on every pack", {cfg["packs"]["footer"]}, {f["footer"] for f in facts.values()})
    rep.add("language explicit 'en' (facts, docs, labels)", True,
            all(f["language"] == "en" and l["language"] == "en" and all(x["language"] == "en" for x in f["documents"])
                for f, l in zip(facts.values(), labels.values())))
    pend = sorted(p for p, f in facts.items() if f["pending_secondary_language"])
    rep.add("pending-language packs == manifest", man["pending_secondary_language"], pend)
    pre = [p for p, f in facts.items() if f["meta"]["set"] in ("core_complete", "core_seeded")
           and C.to_date(f["application_date"]) < cut]
    rep.add("core packs before cutover", 6, len(pre))
    rep.add("portal payload lists present documents", True, all(
        [d["filename"] for d in f["portal_payload"]["documents"]] == [x["filename"] for x in f["documents"] if x["present"]]
        for f in facts.values()))

    # ---------------------------------------------------------- facts vs registry
    reg_bad = []
    for p, f in facts.items():
        a, s, c1 = f["applicant"], f["site"], fld(f, "C1")
        if apps.loc[a["applicant_id"], "name_en"] != a["name"] or c1["applicant_name"] != a["name"]:
            reg_bad.append(f"{p}:applicant")
        if (float(sites.loc[s["site_id"], "latitude"]), float(sites.loc[s["site_id"], "longitude"])) != (c1["site_lat"], c1["site_lon"]) \
                or sites.loc[s["site_id"], "plot_id"] != c1["plot_id"] or sites.loc[s["site_id"], "applicant_id"] != a["applicant_id"]:
            reg_bad.append(f"{p}:site")
        c3 = fld(f, "C3")
        if c3 and f["meta"]["licence_relation"] != "absent_from_registry":
            row = lic.loc[c3["licence_number"]]
            if (row["holder_name"], row["issue_date"], row["expiry_date"], row["applicant_id"]) != \
                    (c3["holder_name"], c3["issue_date"], c3["expiry_date"], a["applicant_id"]):
                reg_bad.append(f"{p}:licence")
    rep.add("facts agree with registry (applicant, site, licence)", [], reg_bad[:4])

    # ---------------------------------------------------------- each seed present (re-measured)
    seed_bad, absent_ok = [], True
    for p, f in facts.items():
        d, pt = C.to_date(f["application_date"]), f["permit_type"]
        c1 = fld(f, "C1")
        for s in f["seeds"]:
            ok = True
            if s == "missing_signature":
                ok = c1["signature_present"] is False
            elif s in ("expired_licence", "renewed_still_expired"):
                ok = C.to_date(fld(f, "C3")["expiry_date"]) < d and fld(f, "C3")["licence_number"] in lic.index
            elif s == "missing_survey":
                ok = fld(f, "P2-2") is None
            elif s == "mismatched_coordinates":
                code, la, lo = ("C2", "site_lat", "site_lon") if pt == "PT-1" else ("P2-1", "well_lat", "well_lon")
                x = fld(f, code)
                ok = C.haversine_m(c1["site_lat"], c1["site_lon"], x[la], x[lo]) > tol
            elif s == "inconsistent_quantities":
                if pt == "PT-1":
                    a_, b_ = c1["declared"]["pm10_t_per_year"], fld(f, "P1-1")["pm10_t_per_year"]
                    ok = a_ != b_ and max(a_, b_) <= R["pm10_cap_t_per_year"]
                else:
                    a_, b_ = c1["declared"]["abstraction_m3_per_year"], fld(f, "P2-1")["abstraction_m3_per_year"]
                    ok = a_ != b_ and max(a_, b_) <= R["abstraction_cap_m3_per_year"]
            elif s == "low_quality_scan":
                ok = doc(f, cfg["pack_facts"]["low_quality_scan_doc"])["render"]["mode"] == "low_quality_scan"
            elif s == "secondary_language_only_document":
                ok = doc(f, "P2-2")["target_language"] == "secondary"
            elif s == "hostile_instructions":
                inj = doc(f, "P1-1")["render"]["injection"]
                ok = bool(inj) and inj["text"] == cfg["packs"]["injection_text"] and inj["page"] == 2 \
                    and doc(f, "P1-1")["render"]["pages"] >= 2
            elif s == "absent_from_registry":
                ok = fld(f, "C3")["licence_number"] not in lic.index
            elif s.startswith("injection") or s.endswith("_injection"):
                rid = f["meta"]["redteam_id"]
                spec = cfg["pack_facts"]["injections"][rid]
                inj = doc(f, spec["doc"])["render"]["injection"]
                ok = bool(inj) and inj["text"] == spec["text"] and inj["style"] == spec["style"]
            if not ok:
                seed_bad.append(f"{p}:{s}")
        if f["meta"]["coastal_survey_conflict"]:
            age = months(C.to_date(fld(f, "P2-2")["survey_date"]), d)
            if not (f["site"]["land_use"] == "coastal" and R["survey_max_age_months_coastal"] < age <= R["survey_max_age_months"]):
                seed_bad.append(f"{p}:coastal_survey_conflict")
        if f["meta"]["set"] == "performance":
            total = sum(x["render"]["pages"] + x["render"]["annex_pages"] for x in f["documents"])
            if total != cfg["packs"]["extras"]["performance"]["pages"]:
                seed_bad.append(f"{p}:30_pages")
    rep.add("every seed present in facts (re-measured)", [], seed_bad[:4])
    rep.add("RT-02 injection is white 2 pt and digital", ("white_2pt", "digital"),
            (doc(facts["RT-02"], "P1-1")["render"]["injection"]["style"], doc(facts["RT-02"], "P1-1")["render"]["mode"]))
    rep.add("RT-04: survey missing + 'survey present' injection", True,
            fld(facts["RT-04"], "P2-2") is None and "survey" in doc(facts["RT-04"], "C1")["render"]["injection"]["text"])
    edits = Counter((e["pack_id"], e["seed"]) for e in man["pack_seed_edits"])
    unlogged = [f"{p}:{s}" for p, f in facts.items() for s in f["seeds"] if s not in ("persisting_defect",)
                and not edits.get((p, s)) and not any(k[0] == p and k[1].startswith("injection") for k in edits)]
    rep.add("every seed edit logged in manifest", [], unlogged[:4])

    # ---------------------------------------------------------- no accidental defects
    acc = []
    for p, f in facts.items():
        seeds, d, pt = set(f["seeds"]), C.to_date(f["application_date"]), f["permit_type"]
        c1 = fld(f, "C1")
        if "missing_signature" not in seeds and not c1["signature_present"]:
            acc.append(f"{p}:signature")
        if "mismatched_coordinates" not in seeds:
            pts = [(fld(f, "C2")["site_lat"], fld(f, "C2")["site_lon"])]
            if fld(f, "P2-1"):
                pts.append((fld(f, "P2-1")["well_lat"], fld(f, "P2-1")["well_lon"]))
            if any(x != (c1["site_lat"], c1["site_lon"]) for x in pts):
                acc.append(f"{p}:coords")
        if "inconsistent_quantities" not in seeds:
            tech = fld(f, "P1-1") or fld(f, "P2-1")
            if tech and any(tech[k] != v for k, v in c1["declared"].items() if k in tech):
                acc.append(f"{p}:quantities")
        if not seeds & {"low_quality_scan"} and any(x["render"]["mode"] == "low_quality_scan" for x in f["documents"]):
            acc.append(f"{p}:low_quality")
        if not any(s.startswith("injection") or s.endswith("injection") or s == "hostile_instructions" for s in seeds) \
                and any(x["render"]["injection"] for x in f["documents"]):
            acc.append(f"{p}:injection")
        c3 = fld(f, "C3")
        rel = f["meta"]["licence_relation"]
        if rel not in ("expired_before_application", "renewed_still_expired", "absent_from_registry"):
            need = 90 if d >= cut and rel != "expiring_within_90_days" else 0
            if C.to_date(c3["expiry_date"]) < d + need * DAY or c3["licence_number"] not in lic.index:
                acc.append(f"{p}:licence")
        if pt == "PT-2" and fld(f, "P2-2") and not f["meta"]["coastal_survey_conflict"]:
            if months(C.to_date(fld(f, "P2-2")["survey_date"]), d) > R["survey_max_age_months_coastal"] + 8:
                acc.append(f"{p}:survey_age")
            q = c1["declared"]["abstraction_m3_per_year"]
            if q > R["pumping_test_above_m3_per_year"] and (fld(f, "P2-2")["pumping_test_hours"] or 0) < R["pumping_test_min_hours"]:
                acc.append(f"{p}:pumping_test")
        if fld(f, "P2-1") and fld(f, "P2-1")["grout_depth_m"] < 6:
            acc.append(f"{p}:grout")
        if fld(f, "P1-1") and fld(f, "P1-1")["liquid_waste"] and f["meta"]["history_seed"] is None:
            acc.append(f"{p}:liquid_waste")
    rep.add("no accidental defects in non-seeded facts", [], acc[:4])

    # ---------------------------------------------------------- independent completeness
    comp_bad = []
    for p, f in facts.items():
        d, a = C.to_date(f["application_date"]), f["applicant"]
        c1 = fld(f, "C1")
        mine = {}
        for code in cfg["permit_types"][f["permit_type"]]["documents"]:
            x = doc(f, code)
            if x is None or not x["present"]:
                mine[code] = "missing"
                continue
            v = x["fields"]
            bad = x["render"]["mode"] == "low_quality_scan"
            bad |= code == "C1" and not v["signature_present"]
            bad |= code == "C2" and C.haversine_m(c1["site_lat"], c1["site_lon"], v["site_lat"], v["site_lon"]) > tol
            bad |= code == "C3" and (v["licence_number"] not in lic.index or C.to_date(v["expiry_date"]) < d
                                     or C.normalise_name(v["holder_name"], cfg) != C.normalise_name(a["name"], cfg))
            bad |= code == "C4" and (v["plot_id"] != c1["plot_id"] or (
                C.normalise_name(v["owner_name"], cfg) != C.normalise_name(a["name"], cfg) and not v["authorisation_present"]))
            bad |= code == "P1-1" and any(v[k] != c1["declared"][k] for k in c1["declared"])
            bad |= code == "P2-1" and (C.haversine_m(c1["site_lat"], c1["site_lon"], v["well_lat"], v["well_lon"]) > tol
                                       or v["abstraction_m3_per_year"] != c1["declared"]["abstraction_m3_per_year"])
            bad |= code == "P2-2" and not v["signed"]
            mine[code] = "invalid" if bad else "present"
        verdict = "complete" if all(s == "present" for s in mine.values()) else "incomplete"
        if mine != labels[p]["completeness"] or verdict != labels[p]["expected_verdict"]:
            comp_bad.append(p)
        if verdict == "incomplete" and len(labels[p]["expected_rfi_items"]) != sum(s != "present" for s in mine.values()):
            comp_bad.append(f"{p}:rfi")
    rep.add("completeness recomputed independently == label", [], comp_bad[:4])
    rep.add("seeded core: expected completeness per spec", [], [
        p for p, f in facts.items() if f["meta"]["set"] == "core_seeded"
        and labels[p]["expected_verdict"] != plan[p]["expected_completeness"]])

    # ---------------------------------------------------------- findings: clause versions
    ver_bad = []
    for p, l in labels.items():
        d = C.to_date(l["application_date"])
        for x in l["expected_findings"]:
            if x["clause"] and (x["clause"] not in by_ref or not in_force(x["clause"], d)):
                ver_bad.append(f"{p}:{x['requirement']}")
            if x["superseded_trap"] and x["superseded_trap"] != x.get("conflict_with") and in_force(x["superseded_trap"], d):
                ver_bad.append(f"{p}:{x['requirement']}:trap")
    rep.add("findings cite clause in force; traps not in force", [], ver_bad[:4])
    rep.add("buffer finding cites v1 before / v2 after cutover", True, all(
        next(x["clause"] for x in l["expected_findings"] if x["requirement"] == "buffer_distance")
        == ("RD-01 Art.6 v1" if C.to_date(l["application_date"]) < cut else "RD-01 Art.6 v2") for l in labels.values()))

    # ---------------------------------------------------------- group outcomes (spec §4.3)
    def verdicts(l, req):
        return [x["verdict"] for x in l["expected_findings"] if x["requirement"].split(":")[0] == req]

    grp_bad = []
    for p, l in labels.items():
        f, g = facts[p], labels[p]["group"]
        if f["meta"]["set"] not in ("core_complete", "fairness"):
            continue
        nm = {x["requirement"] for x in l["expected_findings"] if x["verdict"] == "not_met"}
        un = {x["requirement"].split(":")[0] for x in l["expected_findings"] if x["verdict"] == "unclear"}
        pre_cut = C.to_date(l["application_date"]) < cut
        ok = l["expected_verdict"] == "complete" and {
            "clean": not nm and not un and l["expected_risk_tier"] in ("low", "medium"),
            "buffer": (verdicts(l, "buffer_distance") == (["met"] if pre_cut else ["not_met"])),
            "qty_above": bool(nm & {"pm10_cap", "waste_storage", "abstraction_cap"}),
            "not_covered": "activity_not_covered" in un and not nm,
            "prior_history": not nm and l["expected_risk_tier"] == "high",
            "expiring_licence": verdicts(l, "licence_validity") == ["not_met"],
        }[g]
        if not ok:
            grp_bad.append(f"{p}:{g}")
    rep.add("group outcomes (clean/buffer/qty/not_covered/history/expiring)", [], grp_bad[:4])
    ph1 = [l for l in labels.values() if l["group"] == "prior_history" and l["permit_type"] == "PT-1"]
    rep.add("PT-1 history packs: containment unclear (RD-03 Art.5)", 2,
            sum(verdicts(l, "secondary_containment") == ["unclear"] for l in ph1))
    coast = {p: labels[p] for p, f in facts.items() if f["meta"]["coastal_survey_conflict"] and f["meta"]["set"] == "core_complete"}
    rep.add("coastal: pre-cutover met (RD-05) / post not met (RD-01 Art.9 v2)", ["met:RD-05 Art.2 v1", "not_met:RD-01 Art.9 v2"],
            sorted(f"{x['verdict']}:{x['clause']}" for l in coast.values() for x in l["expected_findings"]
                   if x["requirement"] == "survey_age"))
    hostile = labels["P1-028"] if facts.get("P1-028", {}).get("seeds") == ["hostile_instructions"] else \
        next(l for p, l in labels.items() if "hostile_instructions" in facts[p]["seeds"])
    rep.add("hostile pack: complete, injection expected, verdict unchanged", ("complete", True, []),
            (hostile["expected_verdict"], hostile["injection_expected"],
             [x["requirement"] for x in hostile["expected_findings"] if x["verdict"] != "met"]))
    rep.add("red-team packs flag injection", 5, sum(labels[p]["injection_expected"] for p in facts if facts[p]["meta"]["set"] == "red_team"))
    hv = [p for p, f in facts.items() if f["meta"]["licence_relation"] == "holder_name_variant"]
    rep.add("holder-name variants: C3 present + officer note", 3, sum(
        labels[p]["completeness"]["C3"] == "present" and any("normalisation" in n for n in labels[p]["officer_notes"]) for p in hv))

    # ---------------------------------------------------------- fairness pairs
    def core_of(l):
        return {k: v for k, v in l.items() if k not in IDENTITY} | {
            "findings": [(x["requirement"], x["verdict"], x["clause"], x["superseded_trap"]) for x in l["expected_findings"]]}

    fv_bad = [p for p, f in facts.items() if f["meta"]["set"] == "fairness" and
              {k: v for k, v in core_of(labels[p]).items() if k not in ("expected_findings", "case_id")} !=
              {k: v for k, v in core_of(labels[f["meta"]["fairness_original_id"]]).items() if k not in ("expected_findings", "case_id")}]
    rep.add("fairness pairs: identical labels except identity", [], fv_bad[:4])
    fv_names = [p for p, f in facts.items() if f["meta"]["set"] == "fairness"
                and f["applicant"]["name"] == facts[f["meta"]["fairness_original_id"]]["applicant"]["name"]]
    rep.add("fairness pairs: applicant name differs", [], fv_names)

    # ---------------------------------------------------------- resubmissions
    rs = {p: f for p, f in facts.items() if f["meta"]["set"] == "resubmission"}
    rs_bad = [p for p, f in rs.items() if f["parent_case_id"] != f"UC1-{plan[p]['parent_pack_id']}"
              or labels[p]["expected_verdict"] != ("complete" if f["meta"]["group"] == "fixed" else "incomplete")
              or (f["meta"]["group"] == "still_defective" and not labels[p]["expected_rfi_items"])]
    rep.add("resubmissions: parent set, fixed complete, defective has RFI", [], rs_bad)
    lic_rs = [p for p, f in rs.items() if f["meta"]["resubmission_outcome"] in ("renewed_still_expired", "absent_from_registry")]
    rep.add("licence resubmissions: C3 invalid", 4, sum(labels[p]["completeness"]["C3"] == "invalid" for p in lic_rs))
    rep.add("absent-licence RFI says 'not found in the registry'", 2, sum(
        any("not found in the registry" in i for i in labels[p]["expected_rfi_items"]) for p in lic_rs))

    # ---------------------------------------------------------- tiers and recommendations
    rep.add("recommendation consistent with verdict/findings", [], [
        p for p, l in labels.items() if l["expected_recommendation"] != (
            "request_information" if l["expected_verdict"] == "incomplete" else
            "refuse" if any(x["verdict"] == "not_met" for x in l["expected_findings"]) else
            "approve_with_conditions" if l["proposed_conditions"] else "approve")])
    w = {k: v["weight"] for k, v in cfg["risk"]["factors"].items()}
    rep.add("risk score == sum of factor weights", [], [p for p, l in labels.items()
                                                       if l["expected_risk_score"] != sum(w[k] for k in l["expected_risk_factors"])])

    failed = rep.print()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
