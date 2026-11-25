#!/usr/bin/env python
"""Stage 6: pack facts + labels for all 105 packs.

Reads the pack plan (seeds_manifest.json "packs", Stage 2), the registry CSVs
and the rule chunks (Stage 4). For each pack:
  1. build clean base facts (everything later printed on its documents),
  2. apply its seed edits to the facts (logged in seeds_manifest "pack_seed_edits"),
  3. write <out>/packs/{pack_id}/facts.json,
  4. compute the label with labeller.py -> <out>/labels/{pack_id}.json.
Resubmissions start from their parent's clean base facts; fairness variants
are copies of their original's final facts with the identity swapped.
Exercise content: fictional data.
"""
from __future__ import annotations

import copy
import hashlib
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import labeller  # noqa: E402

DAY = timedelta(days=1)
WASTE_TYPES = {
    "concrete batching plant": ["concrete washout sludge", "cement dust"], "metal fabrication workshop": ["metal swarf", "spent abrasive"],
    "asphalt mixing plant": ["bitumen residue", "filter dust"], "plastic moulding facility": ["plastic offcuts", "spent resin"],
    "food processing plant": ["organic residue", "packaging waste"], "vehicle paint shop": ["paint sludge", "used solvent rags"],
    "aggregate crushing site": ["fines", "oversize rock"], "chemical blending unit": ["empty drums", "off-spec product"],
}
CONTROLS = ["bag filters on extraction points", "water sprays on stockpiles", "enclosed conveyors",
            "wheel wash at the site exit", "covered storage bays"]
AQUIFERS = ["Upper Gravel Aquifer", "Dune Sand Aquifer", "Limestone Aquifer", "Lower Sandstone Aquifer"]
CONTAINMENT = "a bunded concrete area around the drum store"


def pack_rng(seed: int, key: str) -> random.Random:
    """Independent stream per pack (str seeding is deterministic across processes)."""
    return random.Random(f"{seed}:{key}")


def faker_for(cfg, seed: int, key: str):
    return C.make_faker(cfg, int(hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()[:8], 16))


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    for day in range(d.day, 27, -1):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, min(d.day, 28))


class Builder:
    def __init__(self, cfg: dict, seed: int, out: Path):
        self.cfg, self.seed, self.out = cfg, seed, out
        reg = out / "registry"
        self.apps = pd.read_csv(reg / "applicants.csv", dtype=str, keep_default_na=False).set_index("applicant_id")
        self.sites = pd.read_csv(reg / "sites.csv", dtype=str, keep_default_na=False).set_index("site_id")
        self.lic = pd.read_csv(reg / "licences.csv", dtype=str, keep_default_na=False).set_index("licence_number")
        self.plan = {p["pack_id"]: p for p in C.read_json(out / "seeds_manifest.json")["packs"]}
        self.pf = cfg["pack_facts"]
        self.edits: list[dict] = []
        self.base: dict[str, dict] = {}
        self.final: dict[str, dict] = {}

    # ------------------------------------------------------------ helpers
    def log(self, pack_id, seed, doc, field, before, after, note=None):
        self.edits.append(dict(pack_id=pack_id, seed=seed, doc=doc, field=field, before=before, after=after, note=note))

    def doc(self, facts, code):
        return next(x for x in facts["documents"] if x["code"] == code)

    def render(self, r, code) -> dict:
        lo, hi = self.pf["pages"][code]
        return {"mode": "scan" if r.random() < self.cfg["packs"]["scan_share"] else "digital",
                "pages": r.randint(lo, hi), "injection": None, "annex_pages": 0}

    # ------------------------------------------------------------ base facts
    def base_facts(self, p: dict) -> dict:
        cfg, pf = self.cfg, self.pf
        r, fk = pack_rng(self.seed, p["pack_id"]), faker_for(cfg, self.seed, p["pack_id"])
        pt, d = p["permit_type"], C.to_date(p["application_date"])
        a, s = self.apps.loc[p["applicant_id"]], self.sites.loc[p["site_id"]]
        lat, lon = float(s["latitude"]), float(s["longitude"])
        prof = p["profile"]
        name = a["name_en"]
        activity = prof["activity"]

        if p["licence_relation"] == "absent_from_registry":
            parent_d = C.to_date(self.plan[p["parent_pack_id"]]["application_date"])
            issue = parent_d + r.randint(5, 10) * DAY
            lic = {"licence_number": p["licence_number"], "holder_name": name,
                   "issue_date": C.iso(issue), "expiry_date": C.iso(issue + 365 * DAY)}
        else:
            row = self.lic.loc[p["licence_number"]]
            lic = {"licence_number": p["licence_number"], "holder_name": row["holder_name"],
                   "issue_date": row["issue_date"], "expiry_date": row["expiry_date"]}
        signatory = name if a["applicant_type"] == "consultant" else f"{fk.first_name()} {fk.last_name()}"
        declared = ({"pm10_t_per_year": prof["pm10_t_per_year"], "waste_t_per_year": prof["waste_t_per_year"],
                     "waste_storage_days": prof["waste_storage_days"]} if pt == "PT-1"
                    else {"abstraction_m3_per_year": prof["abstraction_m3_per_year"]})
        docs = []

        def add(code, fields):
            docs.append({"code": code, "filename": f"{pf['filenames'][code]}.pdf", "present": True,
                         "language": cfg["languages"]["primary"], "target_language": None,
                         "render": self.render(r, code), "fields": fields})

        add("C1", {"applicant_name": name, "applicant_id": p["applicant_id"], "applicant_type": a["applicant_type"],
                   "permit_type": pt, "site_address": s["address_en"], "site_lat": lat, "site_lon": lon,
                   "plot_id": s["plot_id"], "activity_description": activity, "declared": declared,
                   "submission_date": p["application_date"], "signature_present": True, "signatory": signatory,
                   "licence_number": lic["licence_number"]})
        add("C2", {"site_lat": lat, "site_lon": lon, "plot_id": s["plot_id"], "district": s["district"],
                   "scale": r.choice(["1:1,000", "1:2,500", "1:5,000"]),
                   "drawing_date": C.iso(d - r.randint(5, 60) * DAY)})
        add("C3", {**lic, "activity": activity})
        if a["applicant_type"] == "consultant":
            owner, auth = f"{fk.last_name()} Holdings LLC", True
        else:
            owner, auth = name, False
        add("C4", {"owner_name": owner, "plot_id": s["plot_id"], "authorisation_present": auth,
                   "authorised_party": name if auth else None,
                   "document_type": r.choice(["title deed", "registered lease"]),
                   "document_date": C.iso(d - r.randint(30, 900) * DAY)})
        if pt == "PT-1":
            pm10 = prof["pm10_t_per_year"]
            add("P1-1", {"pollutants": ["PM10"], **declared,
                         "waste_types": WASTE_TYPES.get(activity, ["mixed industrial waste", "packaging waste"]),
                         "liquid_waste": False, "containment_description": None,
                         "control_measures": r.sample(CONTROLS, 2),
                         "monitoring_proposed": pm10 > 0.75 * self.cfg["rules"]["pm10_cap_t_per_year"]})
        else:
            q = prof["abstraction_m3_per_year"]
            add("P2-1", {"well_depth_m": prof["well_depth_m"], "diameter_mm": r.choice([150, 200, 250]),
                         "casing": r.choice(["steel", "uPVC"]), "grout_depth_m": r.randint(6, 15),
                         "abstraction_m3_per_year": q, "well_lat": lat, "well_lon": lon,
                         "driller_registration": f"DR-{r.randrange(10000):04d}", "flow_meter": True})
            lo, hi = pf["survey_age_months_clean"]
            add("P2-2", {"survey_date": C.iso(add_months(d, -r.randint(lo, hi))), "aquifer": r.choice(AQUIFERS),
                         "static_water_level_m": round(r.uniform(8, 60), 1), "tds_mg_l": r.randrange(600, 4500, 50),
                         "surveyor": f"{fk.first_name()} {fk.last_name()}",
                         "surveyor_registration": f"HG-{r.randrange(10000):04d}",
                         "pumping_test_hours": r.choice([24, 36, 48])
                         if q > self.cfg["rules"]["pumping_test_above_m3_per_year"] else None,
                         "signed": True})
        return {
            "pack_id": p["pack_id"], "case_id": f"UC1-{p['pack_id']}", "submission_id": f"SUB-{p['pack_id']}",
            "channel": "portal_api", "permit_type": pt, "department": p["department"],
            "application_date": p["application_date"], "language": p["language"],
            "pending_secondary_language": p["pending_secondary_language"], "target_language": p["target_language"],
            "parent_case_id": f"UC1-{p['parent_pack_id']}" if p["parent_pack_id"] else None,
            "scenario": p["scenario"], "seeds": list(p["seeds"]),
            "applicant": {"applicant_id": p["applicant_id"], "name": name, "applicant_type": a["applicant_type"],
                          "contact_email": a["email"]},
            "site": {"site_id": p["site_id"], "plot_id": s["plot_id"], "address": s["address_en"],
                     "district": s["district"], "land_use": s["land_use"], "latitude": lat, "longitude": lon},
            "documents": docs,
            "meta": {k: p[k] for k in ("set", "group", "uncovered_topic", "history_seed", "quantity_breach",
                                       "coastal_survey_conflict", "redteam_id", "resubmission_outcome",
                                       "fairness_original_id", "licence_relation")},
            "footer": self.cfg["packs"]["footer"],
        }

    # ------------------------------------------------------------ scenario + seed edits
    def scenario_edits(self, facts: dict, p: dict):
        """Facts that define a group's scenario (not defects)."""
        pid = facts["pack_id"]
        d = C.to_date(facts["application_date"])
        if p["coastal_survey_conflict"]:
            s = self.doc(facts, "P2-2")["fields"]
            new = C.iso(add_months(d, -self.pf["coastal_survey_age_months"]))
            self.log(pid, "coastal_survey_conflict", "P2-2", "survey_date", s["survey_date"], new,
                     "coastal site, survey older than 12 but within 24 months")
            s["survey_date"] = new
        if p["history_seed"] and p["permit_type"] == "PT-1":
            x = self.doc(facts, "P1-1")["fields"]
            x["liquid_waste"], x["containment_description"] = True, CONTAINMENT
            self.log(pid, "ambiguous_containment", "P1-1", "liquid_waste", False, True, "RD-03 Art.5 -> unclear")

    def apply_seed(self, facts: dict, seed: str, r: random.Random, p: dict):
        pid, pt = facts["pack_id"], facts["permit_type"]
        if seed == "missing_signature":
            self.doc(facts, "C1")["fields"]["signature_present"] = False
            self.log(pid, seed, "C1", "signature_present", True, False)
        elif seed == "expired_licence":
            self.log(pid, seed, "C3", "expiry_date", None, self.doc(facts, "C3")["fields"]["expiry_date"],
                     "registry licence expired before the application date")
        elif seed == "missing_survey":
            doc = self.doc(facts, "P2-2")
            doc["present"], doc["fields"] = False, None
            self.log(pid, seed, "P2-2", "present", True, False)
        elif seed == "mismatched_coordinates":
            code, la, lo = ("C2", "site_lat", "site_lon") if pt == "PT-1" else ("P2-1", "well_lat", "well_lon")
            x = self.doc(facts, code)["fields"]
            lat, lon = C.destination_point(x[la], x[lo], r.uniform(0, 360), r.uniform(*self.pf["coordinate_offset_m"]))
            before = [x[la], x[lo]]
            x[la], x[lo] = C.round_coord(lat), C.round_coord(lon)
            self.log(pid, seed, code, f"{la},{lo}", before, [x[la], x[lo]])
        elif seed == "inconsistent_quantities":
            if pt == "PT-1":
                x, v = self.doc(facts, "P1-1")["fields"], self.doc(facts, "C1")["fields"]["declared"]["pm10_t_per_year"]
                new = round(v * 1.4, 1) if v * 1.4 <= self.cfg["rules"]["pm10_cap_t_per_year"] else round(v * 0.6, 1)
                self.log(pid, seed, "P1-1", "pm10_t_per_year", x["pm10_t_per_year"], new, "both values under the cap")
                x["pm10_t_per_year"] = new
            else:
                x = self.doc(facts, "P2-1")["fields"]
                v = x["abstraction_m3_per_year"]
                cap = self.cfg["rules"]["abstraction_cap_m3_per_year"]
                new = v + 8000 if v + 8000 <= cap else v - 8000
                self.log(pid, seed, "P2-1", "abstraction_m3_per_year", v, new, "both values under the cap")
                x["abstraction_m3_per_year"] = new
                s = self.doc(facts, "P2-2")
                if s["present"] and max(v, new) > self.cfg["rules"]["pumping_test_above_m3_per_year"]:
                    s["fields"]["pumping_test_hours"] = s["fields"]["pumping_test_hours"] or 24
        elif seed == "low_quality_scan":
            code = self.pf["low_quality_scan_doc"]
            rd = self.doc(facts, code)["render"]
            self.log(pid, seed, code, "render.mode", rd["mode"], "low_quality_scan")
            rd["mode"] = "low_quality_scan"
        elif seed == "secondary_language_only_document":
            doc = self.doc(facts, "P2-2")
            doc["target_language"] = "secondary"
            self.log(pid, seed, "P2-2", "target_language", None, "secondary", "rendered in English until confirmed")
        elif seed == "hostile_instructions":
            self.inject(facts, "R-01", self.cfg["packs"]["injection_text"])
        elif seed in ("hidden_white_text_injection", "secondary_language_injection", "injection_mark_survey_present",
                      "injection_cite_clause", "injection_other_applicant_data"):
            rid = p["redteam_id"]
            self.inject(facts, rid, self.pf["injections"][rid]["text"])
        elif seed in ("renewed_still_expired", "absent_from_registry"):
            self.log(pid, seed, "C3", "licence_number", None, self.doc(facts, "C3")["fields"]["licence_number"],
                     "renewal still expired" if seed == "renewed_still_expired" else "number not in the registry")
        else:
            raise ValueError(f"unknown seed {seed}")

    def inject(self, facts, rid, text):
        spec = self.pf["injections"][rid]
        doc = self.doc(facts, spec["doc"])
        rd = doc["render"]
        rd["mode"] = "digital"  # injections must stay machine-readable (white text would vanish in a scan)
        rd["pages"] = max(rd["pages"], spec["page"])
        rd["injection"] = {"redteam_id": rid, "text": text, "page": spec["page"], "style": spec["style"],
                           "pending_secondary_language": spec.get("pending_secondary_language", False)}
        self.log(facts["pack_id"], f"injection_{rid}", spec["doc"], "render.injection", None, text)

    # ------------------------------------------------------------ pack sets
    def build_new(self, p: dict) -> dict:
        facts = self.base_facts(p)
        self.base[p["pack_id"]] = copy.deepcopy(facts)
        self.scenario_edits(facts, p)
        r = pack_rng(self.seed, p["pack_id"] + ":seeds")
        for seed in p["seeds"]:
            self.apply_seed(facts, seed, r, p)
        if p["set"] == "performance":
            total = sum(x["render"]["pages"] for x in facts["documents"])
            facts["documents"][-1]["render"]["annex_pages"] = self.cfg["packs"]["extras"]["performance"]["pages"] - total
            self.log(p["pack_id"], "performance_padding", facts["documents"][-1]["code"], "render.annex_pages", 0,
                     facts["documents"][-1]["render"]["annex_pages"], "pack padded to 30 pages")
        return facts

    def build_resubmission(self, p: dict) -> dict:
        parent = self.plan[p["parent_pack_id"]]
        facts = copy.deepcopy(self.base[parent["pack_id"]])  # parent's clean facts: every defect fixed
        new = self.base_facts(p)  # for the C3 of this resubmission and identity fields
        d = p["application_date"]
        facts.update({k: new[k] for k in ("pack_id", "case_id", "submission_id", "application_date", "parent_case_id",
                                          "scenario", "seeds", "meta", "pending_secondary_language", "target_language")})
        self.doc(facts, "C1")["fields"]["submission_date"] = d
        if p["licence_relation"] != "parent_licence":
            self.doc(facts, "C3")["fields"] = self.doc(new, "C3")["fields"]
            self.doc(facts, "C1")["fields"]["licence_number"] = p["licence_number"]
        r = pack_rng(self.seed, p["pack_id"] + ":seeds")
        self.scenario_edits(facts, parent)
        for seed in p["seeds"]:
            self.apply_seed(facts, seed, r, p)
        return facts

    def build_fairness(self, p: dict) -> dict:
        facts = copy.deepcopy(self.final[p["fairness_original_id"]])
        a, s = self.apps.loc[p["applicant_id"]], self.sites.loc[p["site_id"]]
        old = facts["applicant"]["name"]
        facts.update({"pack_id": p["pack_id"], "case_id": f"UC1-{p['pack_id']}", "submission_id": f"SUB-{p['pack_id']}",
                      "scenario": p["scenario"], "seeds": list(p["seeds"]),
                      "pending_secondary_language": p["pending_secondary_language"], "target_language": p["target_language"]})
        facts["meta"] = {**facts["meta"], "set": "fairness", "fairness_original_id": p["fairness_original_id"],
                         "licence_relation": "fairness_clone"}
        facts["applicant"] = {"applicant_id": p["applicant_id"], "name": a["name_en"], "applicant_type": a["applicant_type"],
                              "contact_email": a["email"]}
        facts["site"].update({"site_id": p["site_id"], "plot_id": s["plot_id"]})
        c1 = self.doc(facts, "C1")["fields"]
        c1.update({"applicant_name": a["name_en"], "applicant_id": p["applicant_id"], "plot_id": s["plot_id"],
                   "licence_number": p["licence_number"]})
        if c1["signatory"] == old:
            c1["signatory"] = a["name_en"]
        self.doc(facts, "C2")["fields"]["plot_id"] = s["plot_id"]
        self.doc(facts, "C3")["fields"].update({"licence_number": p["licence_number"],
                                                "holder_name": self.lic.loc[p["licence_number"], "holder_name"]})
        c4 = self.doc(facts, "C4")["fields"]
        c4["plot_id"] = s["plot_id"]
        if c4["owner_name"] == old:
            c4["owner_name"] = a["name_en"]
        if c4["authorised_party"] == old:
            c4["authorised_party"] = a["name_en"]
        self.log(p["pack_id"], "fairness_variant", "C1,C3,C4", "applicant identity", old, a["name_en"],
                 f"copy of {p['fairness_original_id']} with name and nationality swapped")
        return facts

    def run(self) -> dict[str, dict]:
        order = sorted(self.plan.values(), key=lambda p: p["pack_id"])
        for p in order:
            if p["set"] in ("core_complete", "core_seeded", "red_team", "performance"):
                self.final[p["pack_id"]] = self.build_new(p)
        for p in order:
            if p["set"] == "resubmission":
                self.final[p["pack_id"]] = self.build_resubmission(p)
        for p in order:
            if p["set"] == "fairness":
                self.final[p["pack_id"]] = self.build_fairness(p)
        return self.final


def main():
    args = C.base_arg_parser(__doc__.splitlines()[0]).parse_args()
    cfg = C.load_config(args.config)
    b = Builder(cfg, C.stage_seed(cfg, "packs", args.seed), args.out)
    facts = b.run()
    ctx = labeller.Context(cfg, args.out)
    for pid in sorted(facts):
        f = facts[pid]
        f["portal_payload"] = {  # spec §4.1 pack metadata as the portal would send it
            "submission_id": f["submission_id"], "permit_type": f["permit_type"],
            "applicant_id": f["applicant"]["applicant_id"], "site_id": f["site"]["site_id"],
            "application_date": f["application_date"], "language": f["language"],
            "contact_email": f["applicant"]["contact_email"], "parent_case_id": f["parent_case_id"],
            "documents": [{"filename": x["filename"], "declared_type": x["code"]} for x in f["documents"] if x["present"]],
        }
        C.write_json(args.out / "packs" / pid / "facts.json", facts[pid])
        C.write_json(args.out / "labels" / f"{pid}.json", labeller.label(facts[pid], ctx))
    b.edits.sort(key=lambda e: (e["pack_id"], e["seed"], e["doc"]))
    C.update_seeds_manifest(args.out, {"pack_seed_edits": b.edits})
    print(f"packs {len(facts)}, seed edits {len(b.edits)} -> {args.out / 'packs'}, {args.out / 'labels'}")


if __name__ == "__main__":
    main()
