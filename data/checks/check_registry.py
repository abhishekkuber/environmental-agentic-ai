#!/usr/bin/env python
"""Stage 2 checks: registry CSVs + pack plan against the spec, schema.sql and config.

Recomputes everything from the CSVs (distances from coordinates, licence
dates against pack dates); does not trust generator internals.
Exit code 1 if any check fails.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generators"))
import common as C  # noqa: E402

DAY = timedelta(days=1)
TABLES = ["applicants", "sites", "protected_areas", "licences", "prior_decisions", "open_cases"]


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, target, actual, ok=None):
        ok = (target == actual) if ok is None else ok
        if isinstance(target, dict) and isinstance(actual, dict) and ok:
            actual = "= target"
        def clip(x):
            s = str(x)
            return s if len(s) <= 48 else s[:45] + "..."
        self.rows.append((name, clip(target), clip(actual), ok))

    def print(self):
        w = max(len(r[0]) for r in self.rows)
        tw = max(len(r[1]) for r in self.rows)
        print(f"{'check':{w}}  {'target':{tw}}  actual")
        for name, t, a, ok in self.rows:
            print(f"{name:{w}}  {t:{tw}}  {a}  {'PASS' if ok else 'FAIL'}")
        failed = sum(1 for r in self.rows if not r[3])
        print(f"\n{len(self.rows) - failed}/{len(self.rows)} passed")
        return failed


def parse_schema(path: Path) -> dict:
    """{table: {col: (type, length)}} and CHECK IN lists for registry tables."""
    sql = path.read_text(encoding="utf-8")
    out, checks = {}, {}
    for m in re.finditer(r"CREATE TABLE registry\.(\w+) \((.*?)\n\);", sql, re.S):
        cols = {}
        for line in m.group(2).splitlines():
            cm = re.match(r"\s{4}(\w+)\s+(N?VARCHAR|CHAR|DECIMAL|INT|DATE|BIT)(?:\((\d+)(?:,\d+)?\))?", line)
            if cm and cm.group(1).upper() not in ("CONSTRAINT", "PRIMARY"):
                cols[cm.group(1)] = (cm.group(2), int(cm.group(3)) if cm.group(3) else None)
        out[m.group(1)] = cols
        for cm in re.finditer(r"CHECK \((\w+) IN \(([^)]*)\)\)", m.group(2)):
            checks[(m.group(1), cm.group(1))] = set(re.findall(r"'([^']*)'", cm.group(2)))
    return out, checks


def main():
    args = C.base_arg_parser("Stage 2 registry checks").parse_args()
    cfg = C.load_config(args.config)
    reg_dir = args.out / "registry"
    rep = Report()
    ref, cut = C.reference_date(cfg), C.cutover_date(cfg)
    R = cfg["registry"]

    df = {t: pd.read_csv(reg_dir / f"{t}.csv", dtype=str, keep_default_na=False) for t in TABLES}
    manifest = json.loads((args.out / "seeds_manifest.json").read_text())
    packs = {p["pack_id"]: p for p in manifest["packs"]}

    # ---------------------------------------------------------- schema conformance
    schema, enums = parse_schema(C.REPO_ROOT / "docs" / "schema.sql")
    bad_cols = [t for t in TABLES if list(df[t].columns) != list(schema[t].keys())]
    rep.add("schema: column names/order match", [], bad_cols)
    too_long = []
    for t in TABLES:
        for col, (typ, ln) in schema[t].items():
            if ln and typ in ("VARCHAR", "NVARCHAR", "CHAR") and df[t][col].str.len().max() > ln:
                too_long.append(f"{t}.{col}")
    rep.add("schema: string lengths fit", [], too_long)
    bad_enum = [f"{t}.{c}" for (t, c), allowed in enums.items()
                if not set(df[t][c]) <= allowed]
    rep.add("schema: CHECK IN values", [], bad_enum)
    for t, col in [("applicants", "applicant_id"), ("sites", "site_id"), ("protected_areas", "protected_area_id"),
                   ("licences", "licence_id"), ("prior_decisions", "decision_id"), ("open_cases", "open_case_id")]:
        rep.add(f"unique PK {t}", True, df[t][col].is_unique)
    rep.add("unique sites.plot_id", True, df["sites"]["plot_id"].is_unique)
    rep.add("unique licences.licence_number", True, df["licences"]["licence_number"].is_unique)

    # ---------------------------------------------------------- counts
    rep.add("count applicants", R["applicants"]["count"], len(df["applicants"]))
    rep.add("count sites", R["sites"]["count"], len(df["sites"]))
    rep.add("count protected_areas", R["protected_areas"]["count"], len(df["protected_areas"]))
    n_lic = len(df["licences"])
    rep.add("count licences (~540)", "520-560", n_lic, 520 <= n_lic <= 560)
    lt = Counter(df["licences"]["licence_type"])
    rep.add("licences trade (~420)", "400-440", lt["trade"], 400 <= lt["trade"] <= 440)
    rep.add("licences prior permits (~120)", "100-140", lt["environmental_permit"] + lt["well_permit"],
            100 <= lt["environmental_permit"] + lt["well_permit"] <= 140)
    rep.add("count prior_decisions", R["prior_decisions"]["count"], len(df["prior_decisions"]))
    rep.add("decision outcomes", R["prior_decisions"]["outcome_counts"], dict(Counter(df["prior_decisions"]["outcome"])))
    rep.add("decisions has_precedent_summary", R["prior_decisions"]["with_precedent_summary"],
            int((df["prior_decisions"]["has_precedent_summary"] == "1").sum()))
    rep.add("count open_cases", R["open_cases"]["count"], len(df["open_cases"]))
    rep.add("open case types", R["open_cases"]["case_types"], dict(Counter(df["open_cases"]["case_type"])))
    rep.add("protected area types", 5, df["protected_areas"]["area_type"].nunique())

    # ---------------------------------------------------------- FK integrity
    A = df["applicants"].set_index("applicant_id")
    S = df["sites"].set_index("site_id")
    fk = []
    for t in ("sites", "licences", "prior_decisions", "open_cases"):
        fk += [f"{t}.applicant_id" for v in df[t]["applicant_id"] if v not in A.index][:1]
    for t in ("licences", "prior_decisions", "open_cases"):
        fk += [f"{t}.site_id" for v in df[t]["site_id"] if v and v not in S.index][:1]
    rep.add("FK integrity", [], fk)
    own = [r.decision_id for r in df["prior_decisions"].itertuples() if S.loc[r.site_id, "applicant_id"] != r.applicant_id]
    own += [r.open_case_id for r in df["open_cases"].itertuples() if r.site_id and S.loc[r.site_id, "applicant_id"] != r.applicant_id]
    own += [r.licence_id for r in df["licences"].itertuples() if r.site_id and S.loc[r.site_id, "applicant_id"] != r.applicant_id]
    rep.add("site belongs to row's applicant", [], own)
    spa = Counter(df["sites"]["applicant_id"])
    rep.add("sites per applicant 1-3", True, all(1 <= spa.get(a, 0) <= 3 for a in A.index))

    # ---------------------------------------------------------- applicants
    ap = df["applicants"]
    share = (ap["applicant_type"] == "company").mean()
    rep.add("company share ~60%", "0.55-0.65", round(share, 3), 0.55 <= share <= 0.65)
    rep.add("nationalities (~10)", R["applicants"]["nationalities"], ap["nationality"].nunique())
    rep.add("preferred_language all 'en'", {"en"}, set(ap["preferred_language"]))
    ar_cols = [f"{t}.{c}" for t in TABLES for c in df[t].columns if c.endswith("_ar") and (df[t][c] != "").any()]
    rep.add("*_ar columns all NULL", [], ar_cols)
    refusal_apps = set(df["prior_decisions"].query("outcome == 'refused'")["applicant_id"])
    rep.add("applicants with >=1 refusal", R["applicants"]["with_prior_refusal"], len(refusal_apps))
    enf_apps = set(df["open_cases"].query("case_type == 'enforcement' and status == 'open'")["applicant_id"])
    rep.add("applicants with open enforcement", R["applicants"]["with_open_enforcement"], len(enf_apps))
    rep.add("NID / CR formats", True,
            all(re.fullmatch(r"NID-\d{8}", v) for v in ap["national_id"] if v)
            and all(re.fullmatch(r"CR-\d{6}", v) for v in ap["company_reg_no"] if v))

    # ---------------------------------------------------------- emails / phones
    dom = re.escape(cfg["email_domain"])
    bad_mail = [e for e in ap["email"] if not re.fullmatch(rf"[a-z0-9.]+@{dom}", e)]
    rep.add(f"emails all @{cfg['email_domain']}", [], bad_mail[:3])
    rep.add("phones fictional +999", True, all(p.startswith("+999 ") for p in ap["phone"]))
    all_text = pd.concat([df[t].astype(str).apply(" ".join, axis=1) for t in TABLES])
    other_domains = sorted({m for s in all_text for m in re.findall(r"@[\w.-]+", s)} - {"@" + cfg["email_domain"]})
    rep.add("no other email domains anywhere", [], other_domains[:3])

    # ---------------------------------------------------------- geography + distance bands
    pas = [r._asdict() for r in df["protected_areas"].itertuples(index=False)]
    for a in pas:
        a["radius_m"] = float(a["radius_m"])
    S_num = df["sites"].assign(lat=df["sites"]["latitude"].astype(float), lon=df["sites"]["longitude"].astype(float))
    outside = [r.site_id for r in S_num.itertuples() if not C.in_bbox(cfg, r.lat, r.lon)]
    rep.add("sites inside bounding box", [], outside[:3])
    coord_fmt = all(re.fullmatch(r"-?\d{1,3}\.\d{6}", v) for t in ("sites",) for c in ("latitude", "longitude")
                    for v in df[t][c])
    rep.add("coordinates DECIMAL(9,6)", True, coord_fmt)
    b = cfg["buffer_bands_m"]
    edge = {r.site_id: min(C.edge_distance_m(r.lat, r.lon, a) for a in pas) for r in S_num.itertuples()}

    def band(d):
        return ("inside" if d < b["inside_both"][1] else "between" if d < b["between_v1_v2"][1]
                else "near" if d < b["near_outside"][1] else "far")

    bands = Counter(band(d) for d in edge.values())
    sb = R["sites"]["seeded_bands"]
    rep.add("sites < 300 m", sb["inside_both"], bands["inside"])
    rep.add("sites 300-500 m", sb["between_v1_v2"], bands["between"])
    rep.add("sites 500-700 m", sb["near_outside"], bands["near"])
    rep.add("sites > 700 m", R["sites"]["count"] - sum(sb.values()), bands["far"])
    margin = min(min(abs(d - x) for x in (300, 500, 700)) for d in edge.values())
    rep.add("no site within 5 m of a band edge", ">=5", round(margin, 1), margin >= 5)
    inside_circle = [s for s, d in edge.items() if d < 0]
    rep.add("no site inside a protected area", [], inside_circle[:3])

    # ---------------------------------------------------------- dates vs reference_date
    def dates(t, c):
        return [date.fromisoformat(v) for v in df[t][c] if v]

    future = []
    for t, c in [("applicants", "created_at"), ("licences", "issue_date"), ("prior_decisions", "decision_date"),
                 ("open_cases", "opened_date")]:
        future += [f"{t}.{c}" for d in dates(t, c) if d > ref][:1]
    rep.add("no event dates after reference_date", [], future)
    rep.add("created_at >= 2018-01-01", True, min(dates("applicants", "created_at")) >= date(2018, 1, 1))
    dd = dates("prior_decisions", "decision_date")
    rep.add("decision dates 2021..ref", True, min(dd) >= date(2021, 1, 1) and max(dd) <= ref)
    L = df["licences"].assign(issue=lambda x: x["issue_date"].map(date.fromisoformat),
                              expiry=lambda x: x["expiry_date"].map(date.fromisoformat))
    rep.add("licence expiry > issue", True, bool((L["expiry"] > L["issue"]).all()))
    derived = L.apply(lambda r: r["status"] == "suspended" or r["status"] == ("expired" if r["expiry"] < ref else "active"), axis=1)
    rep.add("licence status consistent with ref date", True, bool(derived.all()))
    rep.add("suspended licences (background)", R["licences"]["suspended"], int((L["status"] == "suspended").sum()))
    created = A["created_at"].map(date.fromisoformat)
    first_ev = {}
    for t, c in [("licences", "issue_date"), ("prior_decisions", "decision_date"), ("open_cases", "opened_date")]:
        for r in df[t][["applicant_id", c]].itertuples(index=False):
            d = date.fromisoformat(r[1])
            first_ev[r[0]] = min(first_ev.get(r[0], d), d)
    late = [a for a, d in first_ev.items() if created[a] > d]
    rep.add("created_at <= applicant's first event", [], late[:3])

    # ---------------------------------------------------------- clauses cited
    refs = cfg["rules"]["clause_refs"]
    known = {v for x in refs.values() for v in (x.values() if isinstance(x, dict) else [x])}
    v1_from = C.to_date(cfg["rules"]["effective_from"]["RD-01"]["v1"])
    bad_clause, bad_ver = [], []
    for r in df["prior_decisions"].itertuples():
        d = date.fromisoformat(r.decision_date)
        for c in json.loads(r.clauses_cited):
            if c not in known:
                bad_clause.append(c)
            if c.startswith("RD-01") and (
                    (c.endswith("v1") and not (v1_from <= d < cut)) or (c.endswith("v2") and d < cut)):
                bad_ver.append(f"{r.decision_id}:{c}")
    rep.add("clauses_cited in config.clause_refs", [], sorted(set(bad_clause)))
    rep.add("RD-01 version in force on decision date", [], bad_ver[:3])
    flagged = df["prior_decisions"][df["prior_decisions"]["has_precedent_summary"] == "1"]
    v1_cites = sum(any(c.startswith("RD-01") and c.endswith("v1") for c in json.loads(x)) for x in flagged["clauses_cited"])
    rep.add("precedents citing RD-01 v1", R["prior_decisions"]["precedent_cites_rd01_v1"], v1_cites)
    roles = Counter(p["role"] for p in manifest["precedent_plan"])
    rep.add("precedent roles", R["prior_decisions"]["precedent_roles"], dict(roles))
    plan_ids = {p["decision_id"] for p in manifest["precedent_plan"]}
    rep.add("precedent_plan == flagged decisions", True, plan_ids == set(flagged["decision_id"]))

    # ---------------------------------------------------------- pack plan
    core = [p for p in packs.values() if p["set"] in ("core_complete", "core_seeded")]
    sets = Counter(p["set"] for p in packs.values())
    rep.add("packs by set", {"core_complete": 40, "core_seeded": 20, "fairness": 20, "resubmission": 15,
                             "red_team": 5, "performance": 5}, dict(sets))
    exp_groups = {"clean": 8, "buffer": 3, "qty_above": 3, "not_covered": 2, "prior_history": 2, "expiring_licence": 2}
    for pt in ("PT-1", "PT-2"):
        g = Counter(p["group"] for p in core if p["set"] == "core_complete" and p["permit_type"] == pt)
        rep.add(f"{pt} complete groups", exp_groups, dict(g))
    exp_seeded = {
        "PT-1": {"missing_signature": 2, "expired_licence": 2, "mismatched_coordinates": 2,
                 "inconsistent_quantities": 2, "low_quality_scan": 1, "hostile_instructions": 1},
        "PT-2": {"missing_signature": 1, "expired_licence": 1, "expired_licence+missing_survey": 1,
                 "missing_survey": 1, "mismatched_coordinates": 2, "inconsistent_quantities": 2,
                 "low_quality_scan": 1, "secondary_language_only_document": 1}}
    for pt in ("PT-1", "PT-2"):
        g = Counter(p["group"] for p in core if p["set"] == "core_seeded" and p["permit_type"] == pt)
        rep.add(f"{pt} seeded groups", exp_seeded[pt], dict(g))
    ids_ok = sorted(p["pack_id"] for p in core) == sorted(
        [f"P{t}-{i:03d}" for t in (1, 2) for i in range(1, 31)])
    rep.add("core pack IDs P1/P2-001..030", True, ids_ok)
    rep.add("complete packs use 001..020", True,
            all(int(p["pack_id"][-3:]) <= 20 for p in core if p["set"] == "core_complete"))

    pre = [p for p in core if date.fromisoformat(p["application_date"]) < cut]
    rep.add("pre-cutover packs (3+3)", {"PT-1": 3, "PT-2": 3}, dict(Counter(p["permit_type"] for p in pre)))
    rep.add("pre-cutover only clean/buffer", True, all(p["group"] in ("clean", "buffer") for p in pre))
    rep.add("pre_cutover flag matches date", True,
            all(p["pre_cutover"] == (date.fromisoformat(p["application_date"]) < cut) for p in core))
    rng_lo, rng_hi = (C.to_date(x) for x in cfg["application_date_range"])
    rep.add("application dates in range", True,
            all(rng_lo <= date.fromisoformat(p["application_date"]) <= rng_hi for p in packs.values()))

    # pack applicant / site ownership and site bands
    own_bad = [p["pack_id"] for p in packs.values() if S.loc[p["site_id"], "applicant_id"] != p["applicant_id"]]
    rep.add("pack site owned by pack applicant", [], own_bad)
    want_band = lambda p: "between" if p["group"] == "buffer" else "near" if p["near_outside"] else "far"  # noqa: E731
    band_bad = [p["pack_id"] for p in packs.values() if band(edge[p["site_id"]]) != want_band(p)]
    rep.add("pack site distance band", [], band_bad)

    # registry history of pack applicants
    hist_bad = []
    for p in core:
        a = p["applicant_id"]
        exp_ref = p["history_seed"] == "prior_refusal"
        exp_enf = p["history_seed"] == "open_enforcement"
        if (a in refusal_apps) != exp_ref or (a in enf_apps) != exp_enf:
            hist_bad.append(p["pack_id"])
    rep.add("refusal/enforcement only on history packs", [], hist_bad)
    pack_apps = {p["applicant_id"] for p in packs.values()}
    other_oc = set(df["open_cases"].query("case_type != 'enforcement'")["applicant_id"]) & pack_apps
    rep.add("no other open cases on pack applicants", [], sorted(other_oc))

    # licence seeds recomputed from dates
    Lb = L.set_index("licence_number")
    licnum_in_reg = set(L["licence_number"])
    rel_count = Counter()
    lic_bad = []
    for p in packs.values():
        d = date.fromisoformat(p["application_date"])
        num, rel = p["licence_number"], p["licence_relation"]
        if rel == "absent_from_registry":
            rel_count[rel] += 1
            if num in licnum_in_reg or not re.fullmatch(r"TL-\d{6}", num):
                lic_bad.append(p["pack_id"])
            continue
        lic = Lb.loc[num]
        if lic["applicant_id"] != p["applicant_id"] or lic["licence_type"] != "trade":
            lic_bad.append(p["pack_id"])
            continue
        exp = lic["expiry"]
        name_match_raw = lic["holder_name"] == A.loc[p["applicant_id"], "name_en"]
        name_match_norm = (C.normalise_name(lic["holder_name"], cfg)
                           == C.normalise_name(A.loc[p["applicant_id"], "name_en"], cfg))
        if rel in ("expired_before_application", "renewed_still_expired"):
            ok = exp < d
            rel_count["expired_before_application"] += 1
        elif rel == "expiring_within_90_days":
            ok = d < exp < d + 90 * DAY and d >= cut
            rel_count[rel] += 1
        elif rel == "holder_name_variant":
            ok = exp >= d + 90 * DAY and not name_match_raw and name_match_norm
            rel_count[rel] += 1
        else:  # valid / parent_licence / fairness_clone
            ok = exp >= d + 90 * DAY and name_match_raw and lic["issue"] < d
        if not ok:
            lic_bad.append(p["pack_id"])
    rep.add("pack licences match their relation", [], lic_bad)
    seeded = R["licences"]["seeded"]
    rep.add("expired-before-app licences linked (4 core + 2 RS)",
            seeded["expired_before_application"]["core_packs"] + seeded["expired_before_application"]["resubmission_still_expired"],
            rel_count["expired_before_application"])
    rep.add("expiring-within-90 licences linked", seeded["expiring_within_90_days"]["core_packs"],
            rel_count["expiring_within_90_days"])
    rep.add("holder-name variants (clean packs)", seeded["holder_name_variant"], rel_count["holder_name_variant"])
    rep.add("holder variants only in clean group", True,
            all(p["group"] == "clean" for p in packs.values() if p["licence_relation"] == "holder_name_variant"))
    rep.add("licence numbers absent from registry", seeded["absent_from_registry"], rel_count["absent_from_registry"])
    bgk = Counter(x["kind"] for x in manifest["background"])
    rep.add("background expired licences", seeded["expired_before_application"]["background"], bgk["licence_expired_unlinked"])
    rep.add("background expiring licences", seeded["expiring_within_90_days"]["background"], bgk["licence_expiring_unlinked"])
    bg_ok = all((Lb.loc[Lb.index[L["licence_id"] == x["record_id"]][0]]["expiry"] < ref)
                if x["kind"] == "licence_expired_unlinked" else
                (ref < Lb.loc[Lb.index[L["licence_id"] == x["record_id"]][0]]["expiry"] < ref + 90 * DAY)
                if x["kind"] == "licence_expiring_unlinked" else True for x in manifest["background"])
    bg_unlinked = not ({x["record_id"] for x in manifest["background"]}
                       & {p["licence_id"] for p in packs.values() if p["licence_id"]})
    rep.add("background licences dated right + unlinked", True, bg_ok and bg_unlinked)

    # resubmissions
    rs = [p for p in packs.values() if p["set"] == "resubmission"]
    rep.add("resubmissions fixed / still defective", {"fixed": 10, "still_defective": 5}, dict(Counter(p["group"] for p in rs)))
    rep.add("still-defective kinds", {"renewed_still_expired": 2, "absent_from_registry": 2, "persisting_defect": 1},
            dict(Counter(p["resubmission_outcome"] for p in rs if p["group"] == "still_defective")))
    rs_bad = [p["pack_id"] for p in rs if not (
        packs[p["parent_pack_id"]]["set"] == "core_seeded"
        and packs[p["parent_pack_id"]]["expected_completeness"] == "incomplete"
        and date.fromisoformat(p["application_date"]) > date.fromisoformat(packs[p["parent_pack_id"]]["application_date"])
        and p["applicant_id"] == packs[p["parent_pack_id"]]["applicant_id"])]
    rep.add("resubmission parent/date/applicant", [], rs_bad)
    rep.add("resubmission parents distinct", 15, len({p["parent_pack_id"] for p in rs}))

    # fairness
    fv = [p for p in packs.values() if p["set"] == "fairness"]
    fv_bad = []
    for p in fv:
        o = packs[p["fairness_original_id"]]
        sa, so = S.loc[p["site_id"]], S.loc[o["site_id"]]
        la, lo = Lb.loc[p["licence_number"]], Lb.loc[o["licence_number"]]
        aa, ao = A.loc[p["applicant_id"]], A.loc[o["applicant_id"]]
        same = (p["application_date"] == o["application_date"] and p["profile"] == o["profile"]
                and (sa.latitude, sa.longitude, sa.land_use, sa.district) == (so.latitude, so.longitude, so.land_use, so.district)
                and (la.issue_date, la.expiry_date, la.status, la.licence_type) == (lo.issue_date, lo.expiry_date, lo.status, lo.licence_type)
                and aa.applicant_type == ao.applicant_type and aa.created_at == ao.created_at
                and aa.name_en != ao.name_en and aa.nationality != ao.nationality
                and o["set"] == "core_complete"
                and p["applicant_id"] not in refusal_apps | enf_apps and o["applicant_id"] not in refusal_apps | enf_apps)
        hist = (df["prior_decisions"]["applicant_id"].isin([p["applicant_id"], o["applicant_id"]]).any()
                or df["open_cases"]["applicant_id"].isin([p["applicant_id"], o["applicant_id"]]).any()
                or (L["applicant_id"] == p["applicant_id"]).sum() != 1 or (L["applicant_id"] == o["applicant_id"]).sum() != 1)
        if not same or hist:
            fv_bad.append(p["pack_id"])
    rep.add("fairness pairs identical except name/nationality", [], fv_bad)
    rep.add("fairness originals distinct", 20, len({p["fairness_original_id"] for p in fv}))

    # pending secondary language
    pend = [p for p in packs.values() if p["pending_secondary_language"]]
    rep.add("pending_secondary_language packs", 18, len(pend))
    rep.add("pending: complete mixed/secondary", {"mixed": 6, "secondary_only": 4},
            dict(Counter(p["target_language"] for p in pend if p["set"] == "core_complete")))
    rep.add("pending: seeded / fairness / red team", {"core_seeded": 3, "fairness": 4, "red_team": 1},
            dict(Counter(p["set"] for p in pend if p["set"] != "core_complete")))
    rep.add("all packs language 'en' for now", {"en"}, {p["language"] for p in packs.values()})
    rep.add("pending list matches flags", sorted(p["pack_id"] for p in pend), manifest["pending_secondary_language"])

    # precedents plan vs packs
    gold = [p for p in core if p["gold_precedent"]]
    rep.add("gold precedent packs (10+10)", {"PT-1": 10, "PT-2": 10}, dict(Counter(p["permit_type"] for p in gold)))
    close = [x for x in manifest["precedent_plan"] if x["role"] == "close"]
    close_bad = [x["decision_id"] for x in close if not (
        packs[x["target_pack_id"]]["permit_type"] == x["permit_type"]
        and band(edge[df["prior_decisions"].set_index("decision_id").loc[x["decision_id"], "site_id"]])
        == want_band(packs[x["target_pack_id"]])
        and x["decision_date"] < packs[x["target_pack_id"]]["application_date"])]
    rep.add("close matches: same type, band, earlier date", [], close_bad)
    decoy_bad = [x["decision_id"] for x in manifest["precedent_plan"] if x["role"] == "decoy"
                 and packs[x["target_pack_id"]]["permit_type"] == x["permit_type"]]
    rep.add("decoys: other permit type", [], decoy_bad)

    # every registry seed listed
    seed_types = Counter(s["seed_type"] for s in manifest["registry_seeds"])
    rep.add("manifest: band-site seeds", sum(sb.values()),
            seed_types["site_inside_v1_v2_buffer"] + seed_types["site_between_v1_v2_buffer"] + seed_types["site_near_outside_buffer"])
    rep.add("manifest: refusal / enforcement applicants", (30, 12),
            (seed_types["applicant_prior_refusal"], seed_types["applicant_open_enforcement"]))
    rep.add("manifest: every seed has a scenario", True, all(s.get("scenario") for s in manifest["registry_seeds"]))

    failed = rep.print()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
