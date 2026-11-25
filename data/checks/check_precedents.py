#!/usr/bin/env python
"""Stage 5 checks: precedents.jsonl and eval/precedents_gold.jsonl.

Exit code 1 if any check fails.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generators"))
import common as C  # noqa: E402
from check_registry import Report  # noqa: E402
from gen_precedents import FILLERS  # noqa: E402


def jl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def main():
    args = C.base_arg_parser("Stage 5 precedent checks").parse_args()
    cfg = C.load_config(args.config)
    rep = Report()
    precs = jl(args.out / "precedents" / "precedents.jsonl")
    gold = jl(args.out / "eval" / "precedents_gold.jsonl")
    man = C.read_json(args.out / "seeds_manifest.json")
    plan = {p["decision_id"]: p for p in man["precedent_plan"]}
    packs = {p["pack_id"]: p for p in man["packs"]}
    decs = pd.read_csv(args.out / "registry" / "prior_decisions.csv", dtype=str).set_index("decision_id")
    apps = pd.read_csv(args.out / "registry" / "applicants.csv", dtype=str)
    by_id = {p["id"]: p for p in precs}
    R = cfg["registry"]["prior_decisions"]

    # ---------------------------------------------------------- precedents
    rep.add("precedent count", R["with_precedent_summary"], len(precs))
    rep.add("precedent ids unique", True, len(by_id) == len(precs))
    flagged = set(decs.index[decs["has_precedent_summary"] == "1"])
    rep.add("one precedent per flagged decision", True, {p["decision_id"] for p in precs} == flagged)
    words = [len(p["summary"].split()) for p in precs]
    rep.add("summary length 150-250 words", "150-250", f"{min(words)}-{max(words)}",
            all(150 <= w <= 250 for w in words))
    rep.add("generic filler sentences per summary <= 2", True,
            all(sum(f in p["summary"] for f in FILLERS) <= 2 for p in precs))
    rep.add("summaries all different", len(precs), len({p["summary"] for p in precs}))
    mismatch = [p["id"] for p in precs if not (
        decs.loc[p["decision_id"], "permit_type"] == p["permit_type"]
        and decs.loc[p["decision_id"], "outcome"] == p["outcome"]
        and decs.loc[p["decision_id"], "decision_date"] == p["decision_date"]
        and json.loads(decs.loc[p["decision_id"], "clauses_cited"]) == p["clauses_cited"])]
    rep.add("type/outcome/date/clauses match registry", [], mismatch)
    rep.add("every cited clause named in summary", [],
            [p["id"] for p in precs if not all(c in p["summary"] for c in p["clauses_cited"])])
    rep.add("outcome stated in summary", [], [p["id"] for p in precs if not p["summary"].endswith(
        {"approved": "Outcome: approved.", "approved_with_conditions": "Outcome: approved with conditions.",
         "refused": "Outcome: refused."}[p["outcome"]])])
    chunks_path = args.out / "rules" / "chunks.jsonl"
    if chunks_path.exists():
        refs = {c["clause_ref"] for c in jl(chunks_path)}
        rep.add("cited clauses exist in chunks.jsonl", [], sorted({c for p in precs for c in p["clauses_cited"]} - refs))
    names = [n for n in apps["name_en"]]
    leaks = [p["id"] for p in precs if any(n in p["summary"] for n in names)
             or re.search(r"@|APP-\d|NID-|CR-\d|TL-\d|\+999", p["summary"])]
    rep.add("no applicant names, IDs or contacts", [], leaks)
    rep.add("no role/target leaked into index records", True,
            all("role" not in p and "target_pack_id" not in p for p in precs))
    rep.add("usage marked context only", {"context_only_not_a_rule"}, {p["usage"] for p in precs})
    rep.add("language field", {"en"}, {p["language"] for p in precs})

    # ---------------------------------------------------------- seeded roles
    pm = {x["precedent_id"]: x for x in man["precedents"]}
    rep.add("roles (close/distant/decoy/generic)", R["precedent_roles"], dict(Counter(x["role"] for x in pm.values())))
    sup = [p for p in precs if p["cites_superseded"]]
    rep.add("precedents citing superseded RD-01 v1", R["precedent_cites_rd01_v1"], len(sup))
    rep.add("cites_superseded == has RD-01 v1 clause", True,
            all(p["cites_superseded"] == any(c.startswith("RD-01") and c.endswith(" v1") for c in p["clauses_cited"])
                for p in precs))
    close_bad, decoy_bad, dist_bad = [], [], []
    for pid, x in pm.items():
        p, pl = by_id[pid], plan[x["decision_id"]]
        if x["role"] in ("close", "distant", "decoy"):
            t = packs[x["target_pack_id"]]
            key = "pm10_t_per_year" if t["permit_type"] == "PT-1" else "abstraction_m3_per_year"
            if x["role"] == "close" and not (
                    p["permit_type"] == t["permit_type"] and pl["profile"]["activity"] == t["profile"]["activity"]
                    and pl["site_band"] == t["site_band"] and p["decision_date"] < t["application_date"]
                    and abs(pl["profile"][key] - t["profile"][key]) <= 0.15 * t["profile"][key] + 1):
                close_bad.append(pid)
            if x["role"] == "decoy" and not (p["permit_type"] != t["permit_type"]
                                             and t["profile"]["activity"] in p["summary"]):
                decoy_bad.append(pid)
            if x["role"] == "distant" and not (p["permit_type"] == t["permit_type"]
                                               and pl["profile"]["activity"] != t["profile"]["activity"]):
                dist_bad.append(pid)
    rep.add("close: same type, activity, band, +-15% qty, earlier", [], close_bad)
    rep.add("decoy: other type, mentions target activity", [], decoy_bad)
    rep.add("distant: same type, different activity", [], dist_bad)

    # ---------------------------------------------------------- gold
    gold_packs = sorted(p["pack_id"] for p in packs.values() if p["gold_precedent"])
    rep.add("gold covers the 20 gold packs", gold_packs, sorted(g["pack_id"] for g in gold))
    g_bad = []
    for g in gold:
        t = packs[g["pack_id"]]
        top = g["expected_top3"]
        ok = (len(top) == 3 and len(set(top)) == 3 and all(i in by_id for i in top)
              and top[0] == g["close_match"] and pm[g["close_match"]]["target_pack_id"] == g["pack_id"]
              and all(by_id[i]["permit_type"] == t["permit_type"] for i in top)
              and all(by_id[i]["decision_date"] < t["application_date"] for i in top)
              and not set(top) & set(g["must_not_rank_top3"])
              and all(pm[i]["role"] != "decoy" for i in top))
        if not ok:
            g_bad.append(g["pack_id"])
    rep.add("gold top-3 valid (close first, same type, earlier, no decoy)", [], g_bad)
    rep.add("gold must_not includes paired decoy/distant", True,
            all(set(g["must_not_rank_top3"]) == {pid for pid, x in pm.items() if x["target_pack_id"] == g["pack_id"]
                                                 and x["role"] in ("decoy", "distant")} for g in gold))
    rep.add("gold groups", {"clean": 8, "buffer": 4, "qty_above": 4, "not_covered": 2, "prior_history": 2},
            dict(Counter(g["group"] for g in gold)))

    failed = rep.print()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
