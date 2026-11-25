#!/usr/bin/env python
"""Stage 5: precedent summaries + gold top-3 for 20 core packs.

Reads the registry CSVs and the precedent_plan / packs sections of
seeds_manifest.json (Stage 2). Writes:
  <out>/precedents/precedents.jsonl   60 summaries (150-250 words), context only, never a rule
  <out>/eval/precedents_gold.jsonl    expected top-3 precedent IDs for the 20 gold packs
and the "precedents" section of seeds_manifest.json.

Summaries are template-based and anonymised (no applicant names or contacts).
Roles (close/distant/decoy/generic) are kept out of precedents.jsonl so the
index doesn't leak the answer; they live in the manifest and the gold file.
Exercise content: fictional data.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

PERMIT_NAME = {"PT-1": "Environmental NOC", "PT-2": "groundwater well permit"}
AREA_TYPE_TEXT = {"mangrove": "mangrove reserve", "wetland": "wetland", "marine_reserve": "marine reserve",
                  "wildlife_reserve": "wildlife reserve", "aquifer_protection": "aquifer protection zone"}
OUTCOME_TEXT = {"approved": "approved", "approved_with_conditions": "approved with conditions", "refused": "refused"}
EXPECTED_DRIVER = {"clean": {"all_met"}, "buffer": {"buffer"}, "qty_above": {"cap", "waste_storage"},
                   "not_covered": {"monitoring"}, "prior_history": {"monitoring"}}

FILLERS = [  # neutral process sentences; true of every precedent
    "The application was checked for completeness before the technical review began.",
    "The technical reviewer recorded the assessment against each applicable clause.",
    "The decision letter set out the reasons and the clauses relied on.",
    "The case file holds the site plan, the assessment notes and the decision letter.",
    "This summary was prepared for officers as background to later applications.",
    "Officers should read it as context for similar cases, not as a rule.",
]


def an(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def fmt_int(n) -> str:
    return f"{int(n):,}"


class Gen:
    def __init__(self, cfg: dict, seed: int, out: Path):
        self.cfg, self.r = cfg, random.Random(seed)
        reg = out / "registry"
        self.sites = pd.read_csv(reg / "sites.csv", dtype=str).set_index("site_id")
        self.decs = pd.read_csv(reg / "prior_decisions.csv", dtype=str).set_index("decision_id")
        self.areas = [dict(r._asdict()) for r in pd.read_csv(reg / "protected_areas.csv").itertuples(index=False)]
        man = C.read_json(out / "seeds_manifest.json")
        self.plan = sorted(man["precedent_plan"], key=lambda x: x["decision_id"])
        self.packs = {p["pack_id"]: p for p in man["packs"]}
        self.rules = cfg["rules"]

    @staticmethod
    def prec_id(decision_id: str) -> str:
        return "PREC-" + decision_id.split("-")[1]

    def nearest_area(self, site_id: str) -> tuple[float, dict]:
        s = self.sites.loc[site_id]
        lat, lon = float(s["latitude"]), float(s["longitude"])
        return min(((C.edge_distance_m(lat, lon, a), a) for a in self.areas), key=lambda x: x[0])

    # ------------------------------------------------------------ text
    def activity_phrase(self, p: dict) -> str:
        act = p["profile"]["activity"]
        if p["role"] == "decoy":  # lexically similar to its target pack, different permit type
            target = self.packs[p["target_pack_id"]]["profile"]["activity"]
            joint = "supplying water to" if p["permit_type"] == "PT-2" else "next to"
            return f"{act} {joint} {an(target)} {target}"
        return act

    def quantity_sentence(self, p: dict) -> str:
        q, r = p["profile"], self.rules
        if p["permit_type"] == "PT-1":
            return (f"The emissions and waste summary declared PM10 emissions of {q['pm10_t_per_year']} t/yr, "
                    f"{fmt_int(q['waste_t_per_year'])} t of waste a year and on-site storage of "
                    f"{q['waste_storage_days']} days (limits: {r['pm10_cap_t_per_year']} t/yr and "
                    f"{r['waste_storage_max_days']} days).")
        depth = f", from a well {q['well_depth_m']} m deep" if "well_depth_m" in q else ""
        return (f"The well design declared a planned abstraction of {fmt_int(q['abstraction_m3_per_year'])} m³ a "
                f"year{depth} (limit: {fmt_int(r['abstraction_cap_m3_per_year'])} m³ per well per year).")

    def assessment(self, p: dict, edge: float, area: dict) -> list[str]:
        q, r, pt, drv = p["profile"], self.rules, p["permit_type"], p["driver"]
        d = C.to_date(p["decision_date"])
        ver = "v1" if d < C.cutover_date(self.cfg) else "v2"
        if drv == "all_met":
            return ["Every declared quantity was within its limit and all required documents were present and consistent.",
                    "No condition was considered necessary."]
        if drv == "monitoring":
            if pt == "PT-1":
                return [f"The PM10 figure was within the limit but above 75% of it, so continuous particulate "
                        f"monitoring with quarterly reporting was attached as a condition.",
                        "All other requirements were met."]
            return ["The abstraction figure was within the limit but close to it, so a calibrated flow meter and "
                    "annual abstraction returns were attached as conditions.", "All other requirements were met."]
        if drv == "containment":
            return ["The officer accepted the liquid-waste storage in principle but required the secondary containment "
                    "to be upgraded before operation.",
                    "The guideline gives no numeric standard for adequate containment, so the condition was set case by case."]
        if drv == "survey_update":
            return ["The hydrogeological survey was close to the end of its validity window, so an updated survey "
                    "within 12 months was made a condition.", "All other requirements were met."]
        if drv == "buffer":
            buf = r["buffer_m"]["v2"] if ver == "v2" and d >= C.to_date("2023-01-01") else r["buffer_m"]["v1"]
            return [f"The site lies about {round(edge, -1):.0f} m from the edge of a {AREA_TYPE_TEXT[area['area_type']]}, "
                    f"inside the {buf} m protected-area buffer in force on the decision date.",
                    "Conditions cannot cure a location inside the buffer, so the application was refused."]
        if drv == "cap":
            if pt == "PT-1":
                return [f"Declared PM10 emissions of {q['pm10_t_per_year']} t/yr exceeded the "
                        f"{r['pm10_cap_t_per_year']} t/yr limit, and no abatement plan was offered.",
                        "The application was refused on that ground."]
            return [f"Planned abstraction of {fmt_int(q['abstraction_m3_per_year'])} m³ a year exceeded the "
                    f"{fmt_int(r['abstraction_cap_m3_per_year'])} m³ limit, and no hydrogeological impact assessment "
                    "was submitted.", "The application was refused on that ground."]
        if drv == "waste_storage":
            return [f"Proposed on-site waste storage of {q['waste_storage_days']} days exceeded the "
                    f"{r['waste_storage_max_days']}-day maximum.", "The application was refused on that ground."]
        if drv == "survey_age":
            return [f"The hydrogeological survey was more than {r['survey_max_age_months']} months old on the "
                    "application date.", "The application was refused pending a current survey."]
        return ["The application remained incomplete after a request for information, because a required document "
                "was not provided.", "The application was refused as incomplete."]

    def key_factors(self, p: dict, edge: float, area: dict, land_use: str) -> list[str]:
        q, pt = p["profile"], p["permit_type"]
        main = (f"PM10 {q['pm10_t_per_year']} t/yr" if pt == "PT-1"
                else f"abstraction {fmt_int(q['abstraction_m3_per_year'])} m3/yr")
        out = [f"activity: {self.activity_phrase(p)}", main]
        if pt == "PT-1":
            out.append(f"waste storage {q['waste_storage_days']} days")
        out += [f"{round(edge, -1):.0f} m from {AREA_TYPE_TEXT[area['area_type']]}", f"land use: {land_use.replace('_', ' ')}",
                f"outcome: {OUTCOME_TEXT[p['outcome']]}", f"driver: {p['driver'].replace('_', ' ')}"]
        return out

    def summary(self, p: dict, edge: float, area: dict, site) -> str:
        r, cfg, pt = self.r, self.cfg, p["permit_type"]
        d = C.to_date(p["decision_date"])
        act = self.activity_phrase(p)
        verb = {"approved": "approved", "approved_with_conditions": "approved, with conditions,",
                "refused": "refused"}[p["outcome"]]
        art = "an" if pt == "PT-1" else "a"
        opener = r.choice([
            f"On {C.iso(d)} the Authority {verb} {art} {PERMIT_NAME[pt]} application for {an(act)} {act} in {site['district']}.",
            f"This {PERMIT_NAME[pt]} application for {an(act)} {act} in {site['district']} was decided on "
            f"{C.iso(d)} and {OUTCOME_TEXT[p['outcome']]}.",
        ])
        area_t = AREA_TYPE_TEXT[area["area_type"]]
        site_s = (f"The site has {site['land_use'].replace('_', ' ')} land use and lies about "
                  f"{fmt_int(round(edge, -1))} m from the edge of the nearest protected area, {an(area_t)} {area_t}.")
        core = [opener, site_s]
        if p["driver"] != "buffer":
            buf = self.rules["buffer_m"]["v2" if d >= C.cutover_date(cfg) else "v1"]
            core.append(f"At that distance the site is outside the {buf} m buffer that applied on the decision date.")
        if p["driver"] != "documents":
            names = [cfg["document_names"][c].lower() for c in cfg["permit_types"][pt]["documents"]]
            core.append(f"The pack contained all {len(names)} required documents: {', '.join(names[:-1])} and {names[-1]}.")
        core.append(self.quantity_sentence(p))
        q = p["profile"]
        if pt == "PT-2" and q["abstraction_m3_per_year"] > self.rules["pumping_test_above_m3_per_year"] \
                and p["driver"] not in ("survey_age", "documents"):
            core.append(f"Because planned abstraction exceeded {fmt_int(self.rules['pumping_test_above_m3_per_year'])} m³ "
                        f"a year, the survey included a pumping test of at least {self.rules['pumping_test_min_hours']} hours.")
        core += self.assessment(p, edge, area)
        tail = [f"Clauses relied on: {', '.join(p['clauses_cited'])}, assessed as in force on the decision date.",
                f"Outcome: {OUTCOME_TEXT[p['outcome']]}."]
        fillers = FILLERS[:]
        r.shuffle(fillers)
        while len(" ".join(core + tail).split()) < 150 and fillers:
            core.append(fillers.pop())
        return " ".join(core + tail)

    # ------------------------------------------------------------ build
    def build(self):
        recs, man = [], []
        for p in self.plan:
            dec = self.decs.loc[p["decision_id"]]
            assert dec["permit_type"] == p["permit_type"] and dec["outcome"] == p["outcome"]
            assert json.loads(dec["clauses_cited"]) == p["clauses_cited"]
            site = self.sites.loc[dec["site_id"]]
            edge, area = self.nearest_area(dec["site_id"])
            pid = self.prec_id(p["decision_id"])
            recs.append(dict(
                id=pid, decision_id=p["decision_id"], permit_type=p["permit_type"], outcome=p["outcome"],
                decision_date=p["decision_date"], language=self.cfg["languages"]["primary"],
                activity=self.activity_phrase(p), quantities={k: v for k, v in p["profile"].items() if k != "activity"},
                site_characteristics=dict(district=site["district"], land_use=site["land_use"],
                                          edge_distance_m=round(edge, 1), nearest_area_type=area["area_type"]),
                key_factors=self.key_factors(p, edge, area, site["land_use"]),
                clauses_cited=p["clauses_cited"], cites_superseded=p["cites_rd01_v1"],
                summary=self.summary(p, edge, area, site), usage="context_only_not_a_rule",
            ))
            man.append(dict(precedent_id=pid, decision_id=p["decision_id"], role=p["role"],
                            target_pack_id=p["target_pack_id"], cites_rd01_v1=p["cites_rd01_v1"],
                            scenario="precedent_relevance" if p["role"] != "generic" else "background"))
        self.recs = recs
        self.man = man
        return recs

    def score(self, prec: dict, plan: dict, pack: dict, pack_land_use: str) -> float:
        s = 0.0
        if plan["profile"]["activity"] == pack["profile"]["activity"]:
            s += 4
        if plan["driver"] in EXPECTED_DRIVER[pack["group"]]:
            s += 3
        if plan["site_band"] == pack["site_band"]:
            s += 2
        key = "pm10_t_per_year" if pack["permit_type"] == "PT-1" else "abstraction_m3_per_year"
        a, b = plan["profile"][key], pack["profile"][key]
        if abs(a - b) <= 0.25 * b:
            s += 2
        if prec["site_characteristics"]["land_use"] == pack_land_use:
            s += 1
        return s

    def gold(self) -> list[dict]:
        by_dec = {r["decision_id"]: r for r in self.recs}
        rows = []
        for pack in sorted((p for p in self.packs.values() if p["gold_precedent"]), key=lambda x: x["pack_id"]):
            land_use = self.sites.loc[pack["site_id"], "land_use"]
            # decoys and this pack's own distant match are never relevant to it
            cands = [p for p in self.plan if p["permit_type"] == pack["permit_type"] and p["role"] != "decoy"
                     and not (p["role"] == "distant" and p["target_pack_id"] == pack["pack_id"])
                     and p["decision_date"] < pack["application_date"]]
            scored = sorted(((self.score(by_dec[p["decision_id"]], p, pack, land_use), p["decision_id"]) for p in cands),
                            key=lambda x: (-x[0], x[1]))
            close = next(p for p in self.plan if p["role"] == "close" and p["target_pack_id"] == pack["pack_id"])
            ranked = [close["decision_id"]] + [d for _, d in scored if d != close["decision_id"]]
            top3 = ranked[:3]
            score_of = dict((d, s) for s, d in scored)
            must_not = sorted(self.prec_id(p["decision_id"]) for p in self.plan
                              if p["target_pack_id"] == pack["pack_id"] and p["role"] in ("decoy", "distant"))
            rows.append(dict(
                pack_id=pack["pack_id"], permit_type=pack["permit_type"], application_date=pack["application_date"],
                language=pack["language"], group=pack["group"],
                expected_top3=[self.prec_id(d) for d in top3],
                close_match=self.prec_id(close["decision_id"]),
                scores={self.prec_id(d): score_of[d] for d in top3},
                must_not_rank_top3=must_not,
                rationale="Close match first; then highest relevance score among same-permit-type precedents dated "
                          "before the application (activity +4, outcome driver +3, distance band +2, main quantity "
                          "within 25% +2, land use +1). Decoys excluded (A-26).",
            ))
        return rows


def main():
    args = C.base_arg_parser(__doc__.splitlines()[0]).parse_args()
    cfg = C.load_config(args.config)
    g = Gen(cfg, C.stage_seed(cfg, "precedents", args.seed), args.out)
    recs = g.build()
    C.write_jsonl(args.out / "precedents" / "precedents.jsonl", recs)
    gold = g.gold()
    C.write_jsonl(args.out / "eval" / "precedents_gold.jsonl", gold)
    C.update_seeds_manifest(args.out, {"precedents": g.man})
    words = [len(r["summary"].split()) for r in recs]
    print(f"precedents {len(recs)} (words {min(words)}-{max(words)}), gold packs {len(gold)}")


if __name__ == "__main__":
    main()
