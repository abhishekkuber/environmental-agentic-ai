#!/usr/bin/env python
"""Stage 8 checks: evaluation sets and template examples.

Recomputes everything from chunks.jsonl, labels/, facts.json and the manifest; does not
import the generator's logic. Exit code 1 if any check fails.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import docx
import pymupdf as fitz
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generators"))
import common as C  # noqa: E402
from check_registry import Report  # noqa: E402
from check_rules import FORBIDDEN  # noqa: E402

CATALOGUE = {"submit_application", "extract_document", "check_completeness", "get_applicant_history",
             "find_precedents", "buffer_check", "search_rules", "generate_pack", "record_decision",
             "get_case_summary", "get_finding_evidence", "submit_review"}
ASSESSMENT = {"get_applicant_history", "find_precedents", "buffer_check", "search_rules"}
BRIEF_CATEGORIES = {"document_injection", "data_exfiltration", "jailbreak", "cross_department_access"}
TEMPLATE_FOOTER = "Draft: requires officer approval. Exercise content: fictional data."


def jl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def in_force(ch: dict, d: date) -> bool:
    return C.to_date(ch["effective_from"]) <= d and (ch["effective_to"] is None or C.to_date(ch["effective_to"]) > d)


def main():
    args = C.base_arg_parser("Stage 8 evaluation checks").parse_args()
    cfg = C.load_config(args.config)
    out = args.out
    ev = out / "eval"
    rep = Report()
    E = cfg["eval"]
    chunks = {c["clause_ref"]: c for c in jl(out / "rules" / "chunks.jsonl") if c["language"] == "en"}
    clause_ids = {c["clause_id"] for c in chunks.values()}
    man = C.read_json(out / "seeds_manifest.json")
    packs = {p["pack_id"]: p for p in man["packs"]}
    labels = {p: C.read_json(out / "labels" / f"{p}.json") for p in packs}

    # ------------------------------------------------------------ 1-4 retrieval
    rq = jl(ev / "retrieval.jsonl")
    sc = Counter(r["scope"] for r in rq)
    rep.add("retrieval questions", E["retrieval"]["total"], len(rq))
    rep.add("retrieval split PT-1/PT-2/cross", (E["retrieval"]["pt1"], E["retrieval"]["pt2"], E["retrieval"]["cross"]),
            (sc["PT-1"], sc["PT-2"], sc["cross"]))
    traps = [r for r in rq if r["superseded_trap_ids"]]
    rep.add("superseded-trap questions", E["retrieval"]["superseded_traps"], len(traps))
    rep.add("is_trap flag == has trap ids", [], [r["query_id"] for r in rq if r["is_trap"] != bool(r["superseded_trap_ids"])])
    rep.add("query ids unique", len(rq), len({r["query_id"] for r in rq}))
    rep.add("query texts unique", len(rq), len({r["query"] for r in rq}))
    rep.add("permit_type consistent with scope", [], [r["query_id"] for r in rq
            if r["permit_type"] != ("any" if r["scope"] == "cross" else r["scope"])])
    bad_exist, bad_force, bad_type = [], [], []
    for r in rq:
        d = C.to_date(r["application_date"])
        need = {"PT-1", "PT-2"} if r["permit_type"] == "any" else {r["permit_type"]}
        for ref in r["relevant_clause_ids"]:
            ch = chunks.get(ref)
            if ch is None:
                bad_exist.append(f"{r['query_id']}:{ref}")
                continue
            if not in_force(ch, d):
                bad_force.append(f"{r['query_id']}:{ref}")
            if not need <= set(ch["permit_types"]):
                bad_type.append(f"{r['query_id']}:{ref}")
        if [chunks[x]["id"] for x in r["relevant_clause_ids"] if x in chunks] != r["relevant_chunk_ids"]:
            bad_exist.append(f"{r['query_id']}:chunk ids")
    rep.add("relevant clauses exist in chunks.jsonl", [], bad_exist)
    rep.add("relevant clauses in force on question date", [], bad_force)
    rep.add("relevant clauses apply to permit type", [], bad_type)
    bad_trap = []
    for r in traps:
        d = C.to_date(r["application_date"])
        for ref in r["superseded_trap_ids"]:
            ch = chunks.get(ref)
            v2 = ref.replace(" v1", " v2")
            if not (ch and ch["doc_id"] == "RD-01" and ch["version"] == "v1" and not in_force(ch, d)
                    and v2 in r["relevant_clause_ids"]):
                bad_trap.append(f"{r['query_id']}:{ref}")
        if d < C.cutover_date(cfg):
            bad_trap.append(f"{r['query_id']}:dated before cutover")
    rep.add("traps: RD-01 v1, not in force, v2 relevant", [], bad_trap)
    covered = {chunks[x]["clause_id"] for r in rq for x in r["relevant_clause_ids"] if x in chunks}
    rep.add("every clause ID covered by a question", sorted(clause_ids), sorted(covered))
    pre = [r for r in rq if any(x.startswith("RD-01 ") for x in r["relevant_clause_ids"])
           and C.to_date(r["application_date"]) < C.cutover_date(cfg)]
    rep.add("pre-cutover RD-01 questions expect v1 and no trap", [], [r["query_id"] for r in pre
            if r["superseded_trap_ids"] or any(x.startswith("RD-01 ") and not x.endswith(" v1") for x in r["relevant_clause_ids"])])
    rep.add("pre-cutover RD-01 questions >= 10 (A3 both ways)", ">= 10", len(pre), len(pre) >= 10)

    # ------------------------------------------------------------ 5 unanswerable
    uq = jl(ev / "unanswerable.jsonl")
    rep.add("unanswerable questions", E["unanswerable"], len(uq))
    rep.add("unanswerable per topic", {t: 6 for t in ["noise", "desalination_brine", "data_centre_cooling_water",
                                                      "temporary_activity", "solar_farm"]},
            dict(Counter(u["topic"] for u in uq)))
    hits = {k: [c["clause_ref"] for c in chunks.values() if re.search(rx, c["text"] + " " + c["title"], re.I)]
            for k, rx in FORBIDDEN.items()}
    rep.add("no clause covers an unanswerable topic", {}, {k: v for k, v in hits.items() if v})
    rep.add("near-miss clauses exist and in force", [], [f"{u['query_id']}:{x}" for u in uq for x in u["near_miss_clause_ids"]
            if x not in chunks or not in_force(chunks[x], C.to_date(u["application_date"]))])
    rep.add("unanswerable expect abstain", [], [u["query_id"] for u in uq if u["expected_behaviour"] != "abstain"])

    # ------------------------------------------------------------ 6 trajectories
    tj = yaml.safe_load((ev / "trajectories.yaml").read_text(encoding="utf-8"))
    sc_ = tj["scenarios"]
    ids = [s["id"] for s in sc_]
    rep.add("trajectory scenarios", E["trajectories"], len(sc_))
    rep.add("A1-A8 present once", [f"A{i}" for i in range(1, 9)], sorted(i for i in ids if i.startswith("A")))
    rep.add("trajectory ids unique", len(ids), len(set(ids)))
    rep.add("trajectory packs exist", [], [s["id"] for s in sc_ if s["pack_id"] not in packs])
    rep.add("tools from the catalogue", [], [f"{s['id']}:{t['tool']}" for s in sc_ for t in s["expected_tools"]
            if t["tool"] not in CATALOGUE] + [f"{s['id']}:{m}" for s in sc_ for m in s["must_not_call"] if m not in CATALOGUE])
    rep.add("must_not_call disjoint from expected tools", [], [s["id"] for s in sc_
            if {t["tool"] for t in s["expected_tools"]} & set(s["must_not_call"])])
    rep.add("supervisor steps within budget (12)", [], [s["id"] for s in sc_
            if s["expected_supervisor_steps"] > tj["max_steps"]])
    bad_rd = []
    for s in sc_:
        names = [t["tool"] for t in s["expected_tools"]]
        if "record_decision" in names:
            appr = [e for e in s["officer_events"] if e["event"] == "approval"]
            rd = next(t for t in s["expected_tools"] if t["tool"] == "record_decision")
            if not appr or rd.get("after_event") != "approval" or names[-1] != "record_decision":
                bad_rd.append(s["id"])
    rep.add("record_decision only after an approval event", [], bad_rd)
    rep.add("only A6 records a decision", ["A6"], [s["id"] for s in sc_ if "record_decision" in
                                                   [t["tool"] for t in s["expected_tools"]]])

    def expected_end(s):
        lab = labels[s["pack_id"]]
        if s["flow"] == "access_denied":
            return "denied"
        if s["flow"] in ("dependency_failure", "step_budget"):
            return "failed_to_queue"
        if s["flow"] == "officer_review":
            return "completed"
        if lab["injection_expected"]:
            return "routed_to_officer"
        return "rfi_drafted" if lab["expected_verdict"] == "incomplete" else "awaiting_review"
    rep.add("end state agrees with label", [], [f"{s['id']}:{s['expected_end_state']}" for s in sc_
            if s["expected_end_state"] != expected_end(s)])
    rep.add("injection scenarios: work queue injection_suspected", [], [s["id"] for s in sc_
            if s["flow"] == "standard" and labels[s["pack_id"]]["injection_expected"]
            and s["expected_work_queue_reason"] != "injection_suspected"])
    rep.add("incomplete packs: no assessment tools (A2)", [], [s["id"] for s in sc_ if s["flow"] == "standard"
            and labels[s["pack_id"]]["expected_verdict"] == "incomplete"
            and ({t["tool"] for t in s["expected_tools"]} & ASSESSMENT or not ASSESSMENT <= set(s["must_not_call"]))])
    bad_cnt = []
    for s in sc_:
        lab = labels[s["pack_id"]]
        for t in s["expected_tools"]:
            if t["tool"] == "extract_document" and t.get("outcome") != "error" and \
                    t["count"] != sum(1 for d in C.read_json(out / "packs" / s["pack_id"] / "facts.json")["documents"] if d["present"]):
                bad_cnt.append(f"{s['id']}:extract")
            if t["tool"] == "search_rules" and t.get("outcome") != "error" and t["count"] != len(lab["expected_findings"]):
                bad_cnt.append(f"{s['id']}:search")
    rep.add("tool counts match documents / requirements", [], bad_cnt)
    a6 = next(s for s in sc_ if s["id"] == "A6")
    rej = [e for e in a6["officer_events"] if e["event"] == "officer_rejects_finding"]
    rep.add("A6: rejects an unclear finding with a reason", True, len(rej) == 1 and bool(rej[0]["reason"]) and any(
        f["requirement"] == rej[0]["requirement"] and f["verdict"] == "unclear" for f in labels["P1-001"]["expected_findings"]))
    rep.add("A6: high tier, section head approves with reason", True,
            labels["P1-001"]["expected_route_to_role"] == "section_head"
            and any(e["event"] == "approval" and e["role"] == "section_head" and e["reason"] for e in a6["officer_events"]))
    rep.add("superseded traps never in expected citations", [], [s["id"] for s in sc_ for t in s["expected_tools"]
            if set(t.get("expected_citations", [])) & set(s["must_not_cite_as_rule"])])
    rep.add("abstain at every unclear finding", [], [s["id"] for s in sc_ if s["flow"] in ("standard", "officer_review")
            and labels[s["pack_id"]]["expected_verdict"] == "complete"
            and {f["requirement"] for f in labels[s["pack_id"]]["expected_findings"] if f["verdict"] == "unclear"}
            - {a.get("requirement") for a in s["abstain_points"]}])

    # ------------------------------------------------------------ 7 red team
    rt = yaml.safe_load((ev / "redteam.yaml").read_text(encoding="utf-8"))["cases"]
    rep.add("red-team ids R-01..R-21 exactly once", [f"R-{i:02d}" for i in range(1, 22)], sorted(c["id"] for c in rt))
    rep.add("red-team count >= 20 (brief)", True, len(rt) >= 20)
    rep.add("brief categories present", sorted(BRIEF_CATEGORIES), sorted(BRIEF_CATEGORIES & {c["brief_category"] for c in rt}))
    rep.add("red-team packs exist", [], [c["id"] for c in rt if c["pack_id"] and c["pack_id"] not in packs])
    bad_doc = []
    for c in [c for c in rt if c["channel"] == "document"]:
        f = C.read_json(out / "packs" / c["pack_id"] / "facts.json")
        inj = [d["render"]["injection"] for d in f["documents"] if d["present"] and d["render"].get("injection")]
        if not any(i["redteam_id"] == c["id"] and i["text"] == c["attack_text"] for i in inj):
            bad_doc.append(c["id"])
        if not labels[c["pack_id"]]["injection_expected"]:
            bad_doc.append(c["id"] + ":label")
    rep.add("document attacks carried in facts.json and labels", [], bad_doc)
    rep.add("R-03 flagged pending second language", True,
            next(c for c in rt if c["id"] == "R-03")["pending_secondary_language"] is True)
    rep.add("every case has text, expectation and pass criterion", [], [c["id"] for c in rt
            if not (c.get("attack_text") and c.get("expected_behaviour") and c.get("pass_criterion"))])
    rep.add("source spec/draft", {"spec": 6, "draft": 15}, dict(sorted(Counter(c["source"] for c in rt).items())))
    precs = {p["id"] for p in jl(out / "precedents" / "precedents.jsonl")}
    rep.add("cited precedent IDs exist", [], [c["id"] for c in rt if c.get("precedent_id") and c["precedent_id"] not in precs])

    # ------------------------------------------------------------ 8 consistency + fairness
    cs = yaml.safe_load((ev / "consistency.yaml").read_text(encoding="utf-8"))
    rep.add("consistency packs x runs", (E["consistency"]["packs"], E["consistency"]["runs"]),
            (len(cs["packs"]), cs["runs_per_pack"]))
    rep.add("consistency packs unique and exist", [], [p["pack_id"] for p in cs["packs"] if p["pack_id"] not in packs]
            + [k for k, v in Counter(p["pack_id"] for p in cs["packs"]).items() if v > 1])
    rep.add("consistency expected == label", [], [p["pack_id"] for p in cs["packs"] if p["expected"] != dict(
        completeness_verdict=labels[p["pack_id"]]["expected_verdict"],
        finding_verdicts={f["requirement"]: f["verdict"] for f in labels[p["pack_id"]]["expected_findings"]},
        risk_tier=labels[p["pack_id"]]["expected_risk_tier"], expected_outcome=labels[p["pack_id"]]["expected_outcome"])
        or len(p["runs"]) != cs["runs_per_pack"]])
    fp = jl(ev / "fairness_pairs.jsonl")
    rep.add("fairness pairs", E["fairness_pairs"], len(fp))
    want = {(p["fairness_original_id"], k) for k, p in packs.items() if p["set"] == "fairness"}
    rep.add("fairness pairs == manifest", sorted(want), sorted((f["original_pack_id"], f["variant_pack_id"]) for f in fp))

    def shared(lab):
        return (lab["expected_verdict"], [(f["requirement"], f["verdict"], f["clause"]) for f in lab["expected_findings"]],
                lab["expected_risk_tier"], lab["expected_recommendation"], lab["expected_outcome"])
    rep.add("fairness: both labels share the outcome", [], [f["pair_id"] for f in fp
            if shared(labels[f["original_pack_id"]]) != shared(labels[f["variant_pack_id"]])])
    rep.add("fairness: expected_shared == label", [], [f["pair_id"] for f in fp if (
        f["expected_shared"]["verdict"], [(x["requirement"], x["verdict"], x["clause"]) for x in f["expected_shared"]["findings"]],
        f["expected_shared"]["risk_tier"], f["expected_shared"]["recommendation"], f["expected_shared"]["outcome"])
        != shared(labels[f["original_pack_id"]])])
    rep.add("fairness: name and nationality differ", [], [f["pair_id"] for f in fp
            if len(set(f["differs_in"]["applicant_name"])) != 2 or len(set(f["differs_in"]["nationality"])) != 2])

    # ------------------------------------------------------------ language + manifest
    every = rq + uq + fp + sc_ + rt
    rep.add("language explicit 'en' on every record", [], [r.get("query_id") or r.get("pair_id") or r.get("id")
                                                          for r in every if r.get("language") != "en"])
    m = man.get("eval", {})
    rep.add("manifest eval section sizes", (150, 30, 30, 21, 20, 20),
            tuple(len(m.get(k, [])) for k in ("retrieval", "unanswerable", "trajectories", "redteam", "consistency", "fairness_pairs")))
    rep.add("manifest lists pending items (R-03, V15)", True,
            {"R-03", "V15"} <= set(m.get("reserved_pending_secondary_language", {}).get("items", [])))
    idx = yaml.safe_load((ev / "index.yaml").read_text(encoding="utf-8"))
    rep.add("index.yaml maps 12 requirements", 12, len(idx["measures"]))

    # ------------------------------------------------------------ 9 templates
    exdir = out / "templates" / "examples"
    examples = {"decision_pack_P1-002": "P1-002", "rfi_letter_P2-023": "P2-023", "decision_pack_P1-001": "P1-001"}
    missing = [f"{n}.{ext}" for n in examples for ext in ("pdf", "docx", "json") if not (exdir / f"{n}.{ext}").exists()]
    rep.add("template examples rendered (pdf, docx, json)", [], missing)
    if not missing:
        bad_list, bad_pdf, bad_docx = [], [], []
        for n, pid in examples.items():
            ctx = C.read_json(exdir / f"{n}.json")
            lab = labels[pid]
            with fitz.open(exdir / f"{n}.pdf") as pdf:
                pages = [" ".join(p.get_text().split()) for p in pdf]
            text = " ".join(pages)
            if not all(TEMPLATE_FOOTER in p for p in pages):
                bad_pdf.append(f"{n}:footer")
            if ctx["template"] == "decision_pack":
                if [(f["requirement"], f["verdict"], f["clause"]) for f in ctx["findings"]] != \
                        [(f["requirement"], f["verdict"], f["clause"]) for f in lab["expected_findings"]]:
                    bad_list.append(n)
                if ctx["recommendation"] != lab["expected_recommendation"] or ctx["risk_tier"] != lab["expected_risk_tier"] \
                        or ctx["conditions"] != lab["proposed_conditions"]:
                    bad_list.append(n + ":rec/tier/conditions")
                for f in lab["expected_findings"]:
                    if f["clause"] and f["clause"] not in text:
                        bad_pdf.append(f"{n}:{f['clause']}")
            else:
                want_items = [k for k, v in lab["completeness"].items() if v != "present"]
                if [i["code"] for i in ctx["rfi_items"]] != want_items:
                    bad_list.append(n)
                for k in want_items:
                    if f"({k})" not in text or lab["completeness_reasons"][k].lower() not in text.lower():
                        bad_pdf.append(f"{n}:{k}")
                deadline = C.iso(C.to_date(ctx["letter_date"]).fromordinal(
                    C.to_date(ctx["letter_date"]).toordinal() + cfg["rules"]["rfi_response_days"]))
                if ctx["deadline"] != deadline or deadline not in text:
                    bad_pdf.append(f"{n}:deadline")
            dx = docx.Document(str(exdir / f"{n}.docx"))
            if dx.sections[0].footer.paragraphs[0].text != TEMPLATE_FOOTER:
                bad_docx.append(n)
        rep.add("examples list exactly the label's findings / RFI items", [], bad_list)
        rep.add("example PDFs show clauses, items, deadline, footer", [], bad_pdf)
        rep.add("example DOCX carry the footer", [], bad_docx)
    tdir = Path(__file__).resolve().parents[1] / "generators" / "render" / "templates" / cfg["languages"]["primary"]
    rep.add("templates exist (decision pack, RFI letter)", True,
            (tdir / "decision_pack.html").exists() and (tdir / "rfi_letter.html").exists())

    sys.exit(1 if rep.print() else 0)


if __name__ == "__main__":
    main()
