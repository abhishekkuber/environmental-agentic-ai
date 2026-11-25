#!/usr/bin/env python
"""Stage 4: rule corpus from hand-written YAML (data/rules_src/*.yaml).

Writes, under <out>/rules/:
  docx/{doc_id}_{version}.docx   one Word file per document version
  pdf/{doc_id}_{version}.pdf     one PDF per document version (WeasyPrint)
  chunks.jsonl                   one record per clause per language (arch §9.1 fields)
  config_seed.sql                config.checklist_items / risk_factors / risk_tiers / thresholds
and the "rules" section of seeds_manifest.json.

Deterministic: fixed build date (reference_date), fixed zip timestamps in DOCX.
Exercise content: fictional rules and data.
"""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import docx
import pymupdf as fitz
import yaml
from docx.shared import Pt
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from weasyprint import HTML

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

RULES_SRC = C.DATA_DIR / "rules_src"
TEMPLATES = Path(__file__).resolve().parent / "render" / "templates"
SOURCE_ROOT = "Rules"  # SharePoint library the files will live in (arch §9.4)

STATUS_LABEL = {"en": {"current": "Current", "superseded": "Superseded"}}
ARTICLE_WORD = {"en": "Article"}
PERMIT_TEXT = {"en": {"PT-1": "PT-1 Environmental NOC", "PT-2": "PT-2 Groundwater well permit"}}


# ------------------------------------------------------------------ loading
def load_sources() -> list[dict]:
    docs = []
    for path in sorted(RULES_SRC.glob("RD-*.yaml")):
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
        d["_file"] = path.name
        docs.append(d)
    return docs


def long_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B')} {d.year}"


def chunk_id(doc_id: str, version: str, article: str, lang: str) -> str:
    """AI Search key: letters, digits, '_', '-', '=' only. arch §9.1: {doc_id}-{version}-{clause_id}."""
    base = f"{doc_id}-{version}-Art{article}".replace(".", "-")
    return base if lang == "en" else f"{base}-{lang}"


def view(doc: dict, lang: str) -> dict:
    """Language-specific view of one document version for templates and chunks."""
    eff_from = C.to_date(doc["effective_from"])
    eff_to = C.to_date(doc["effective_to"]) if doc.get("effective_to") else None
    last_day = (eff_to.toordinal() - 1) if eff_to else None
    clauses = []
    for c in doc["clauses"]:
        clauses.append(dict(
            clause_id=c["clause_id"], article=c["article"], title=c["title"][lang],
            heading=f"{ARTICLE_WORD[lang]} {c['article']}. {c['title'][lang]}",
            permit_types=c["permit_types"], params=c.get("params", {}),
            permit_types_text=", ".join(PERMIT_TEXT[lang][p] for p in c["permit_types"]),
            text=" ".join(c["text"][lang].split()),
        ))
    return dict(
        doc_id=doc["doc_id"], title=doc["doc_title"][lang], version=doc["version"], status=doc["status"],
        effective_from=eff_from, effective_to=eff_to,
        effective_from_text=long_date(eff_from),
        effective_until_text=long_date(date.fromordinal(last_day)) if eff_to else "until replaced",
        effective_to_text=long_date(eff_to) if eff_to else "",
        permit_types=doc["permit_types"],
        permit_types_text=", ".join(PERMIT_TEXT[lang][p] for p in doc["permit_types"]),
        introduction=" ".join(doc["introduction"][lang].split()), clauses=clauses,
    )


# ------------------------------------------------------------------ rendering
def render_pdf(v: dict, lang: str, cfg: dict, out: Path) -> None:
    env = Environment(loader=FileSystemLoader(TEMPLATES / lang), undefined=StrictUndefined, autoescape=True)
    html = env.get_template("rule_document.html").render(
        doc=v, lang=lang, footer=cfg["packs"]["footer"], build_date=cfg["reference_date"],
        status_label=STATUS_LABEL[lang][v["status"]])
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf = HTML(string=html, base_url=str(TEMPLATES / lang)).write_pdf()
    out.write_bytes(pdf)


def fixed_zip(data: bytes, when: tuple) -> bytes:
    """Rewrite a DOCX (zip) with fixed entry timestamps so builds are byte-identical."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=when)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            dst.writestr(zi, src.read(info.filename))
    return buf.getvalue()


def render_docx(v: dict, lang: str, cfg: dict, out: Path) -> None:
    ref = C.reference_date(cfg)
    d = docx.Document()
    cp = d.core_properties
    cp.title = f"{v['doc_id']} {v['title']} ({v['version']})"
    cp.author = cp.last_modified_by = "The Authority (fictional)"
    cp.created = cp.modified = datetime(ref.year, ref.month, ref.day)
    cp.last_printed = datetime(ref.year, ref.month, ref.day)
    cp.revision = 1
    cp.keywords = "Exercise content: fictional data"
    style = d.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(10.5)
    d.add_paragraph("THE AUTHORITY (FICTIONAL ENVIRONMENTAL REGULATOR)")
    d.add_heading(f"{v['doc_id']}: {v['title']}", level=0)
    status = STATUS_LABEL[lang][v["status"]]
    meta = d.add_table(rows=0, cols=2)
    for k, val in (("Version", v["version"]), ("In force from", v["effective_from_text"]),
                   ("In force until", v["effective_until_text"]), ("Status", status),
                   ("Applies to", v["permit_types_text"])):
        row = meta.add_row().cells
        row[0].text, row[1].text = k, val
    if v["status"] == "superseded":
        p = d.add_paragraph()
        p.add_run(f"Superseded. This version applies only to applications submitted before "
                  f"{v['effective_to_text']}.").bold = True
    d.add_paragraph(v["introduction"])
    for c in v["clauses"]:
        d.add_heading(c["heading"], level=2)
        d.add_paragraph(f"Applies to: {c['permit_types_text']}").runs[0].italic = True
        d.add_paragraph(c["text"])
    section = d.sections[0]
    section.header.paragraphs[0].text = f"{v['doc_id']} {v['version']}: {v['title']} ({status})"
    section.footer.paragraphs[0].text = cfg["packs"]["footer"]
    buf = io.BytesIO()
    d.save(buf)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(fixed_zip(buf.getvalue(), (ref.year, ref.month, ref.day, 0, 0, 0)))


def clause_pages(pdf_path: Path, v: dict) -> dict[str, int]:
    """1-based page where each clause heading appears in the rendered PDF."""
    pages = {}
    with fitz.open(pdf_path) as pdf:
        texts = [" ".join(p.get_text().split()) for p in pdf]
    for c in v["clauses"]:
        hit = next((i + 1 for i, t in enumerate(texts) if c["heading"] in t), None)
        if hit is None:
            raise RuntimeError(f"heading not found in PDF: {c['heading']}")
        pages[c["clause_id"]] = hit
    return pages


# ------------------------------------------------------------------ config_seed.sql
def sql_str(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    return "N'" + str(v).replace("'", "''") + "'"


def validation_rules(cfg: dict) -> dict[str, dict]:
    r, t = cfg["rules"], cfg["thresholds"]
    refs = r["clause_refs"]
    legible = {"legible": True, "illegible_clause": refs["legibility"].rsplit(" ", 1)[0]}
    return {
        "C1": {"required_fields": ["applicant_name", "applicant_id", "permit_type", "site_address", "site_lat",
                                   "site_lon", "activity_description", "declared_quantities", "submission_date",
                                   "signature_present"],
               "signature_present": True, "date_valid": True, "coordinates_in_bounding_box": True,
               "clauses": ["RD-07 Art.4"], **legible},
        "C2": {"fields": ["site_lat", "site_lon", "plot_id", "scale"], "coordinates_match": ["C1"],
               "tolerance_m": t["coordinate_tolerance_m"], "clauses": ["RD-07 Art.5"], **legible},
        "C3": {"fields": ["licence_number", "holder_name", "issue_date", "expiry_date"],
               "exists_in_registry": True, "holder_matches_applicant": "normalised (A-27)",
               "expiry_on_or_after": "application_date", "clauses": ["RD-07 Art.3"], **legible},
        "C4": {"fields": ["owner_name", "plot_id", "document_date"], "plot_id_matches": "C2",
               "owner_is_applicant_or_authorised": True, "clauses": ["RD-07 Art.6"], **legible},
        "P1-1": {"fields": ["pollutants", "annual_quantities", "units", "control_measures"],
                 "quantities_match": "C1", "units": ["t/yr", "days"], "clauses": ["RD-07 Art.5"], **legible},
        "P2-1": {"fields": ["well_depth_m", "diameter_mm", "casing", "abstraction_m3_per_year", "well_lat", "well_lon"],
                 "coordinates_match": ["C1", "C2"], "tolerance_m": t["coordinate_tolerance_m"],
                 "abstraction_matches": "C1", "clauses": ["RD-07 Art.5"], **legible},
        "P2-2": {"fields": ["survey_date", "aquifer", "water_level_m", "surveyor"], "present": True,
                 "max_age_months": r["survey_max_age_months"],
                 "max_age_months_coastal_from": {"date": cfg["cutover_date"], "months": r["survey_max_age_months_coastal"]},
                 "clauses": ["RD-05 Art.2", "RD-01 Art.9"], **legible},
    }


def config_seed_sql(cfg: dict, required_docs: dict[str, list[str]]) -> str:
    vr = validation_rules(cfg)
    lines = ["-- Generated by gen_rules.py. Exercise content: fictional data.",
             "-- Populates the config schema of docs/schema.sql. Safe to re-run.",
             "SET NOCOUNT ON;",
             "DELETE FROM config.checklist_items;", "DELETE FROM config.thresholds;",
             "DELETE FROM config.risk_factors;", "DELETE FROM config.risk_tiers;", ""]
    for pt in sorted(required_docs):
        for code in required_docs[pt]:
            lines.append(
                "INSERT INTO config.checklist_items (permit_type, item_code, item_name_en, item_name_ar, required, "
                f"source_clause_id, validation_rules) VALUES ({sql_str(pt)}, {sql_str(code)}, "
                f"{sql_str(cfg['document_names'][code])}, NULL, 1, N'RD-07 Art.2', "
                f"{sql_str(json.dumps(vr[code], sort_keys=True))});")
    lines.append("")
    for code, f in cfg["risk"]["factors"].items():
        lines.append(f"INSERT INTO config.risk_factors (factor_code, description_en, weight, active) VALUES "
                     f"({sql_str(code)}, {sql_str(f['description'])}, {f['weight']:.2f}, 1);")
    lines.append("")
    for tier, t in cfg["risk"]["tiers"].items():
        lines.append(f"INSERT INTO config.risk_tiers (tier, min_score, approver_role) VALUES "
                     f"({sql_str(tier)}, {t['min_score']:.2f}, {sql_str(t['approver_role'])});")
    lines.append("")
    for code, val, unit, desc in thresholds(cfg):
        lines.append(f"INSERT INTO config.thresholds (threshold_code, value_numeric, unit, description_en) VALUES "
                     f"({sql_str(code)}, {val}, {sql_str(unit)}, {sql_str(desc)});")
    return "\n".join(lines) + "\n"


def thresholds(cfg: dict) -> list[tuple]:
    r, t = cfg["rules"], cfg["thresholds"]
    return [
        ("low_conf_field", t["low_confidence_field"], "ratio", "Field flagged below this extraction confidence (A-20)"),
        ("coord_tolerance_m", t["coordinate_tolerance_m"], "m", "Max coordinate difference between documents (RD-07 Art.5)"),
        ("holder_name_normalised_match", t["holder_name_normalised_match"], "flag", "Accept holder name matched after normalisation (A-27)"),
        ("buffer_m_v1", r["buffer_m"]["v1"], "m", "Protected-area buffer, RD-01 Art.6 v1"),
        ("buffer_m_v2", r["buffer_m"]["v2"], "m", "Protected-area buffer, RD-01 Art.6 v2"),
        ("licence_min_remaining_days", r["licence_min_remaining_days"], "days", "Trade licence validity after application date (RD-01 Art.4 v2)"),
        ("survey_max_age_months", r["survey_max_age_months"], "months", "Hydrogeological survey age (RD-05 Art.2)"),
        ("survey_max_age_months_coastal", r["survey_max_age_months_coastal"], "months", "Survey age for coastal sites (RD-01 Art.9 v2)"),
        ("pm10_cap_t_per_year", r["pm10_cap_t_per_year"], "t/yr", "PM10 emission limit (RD-02 Art.3)"),
        ("waste_storage_max_days", r["waste_storage_max_days"], "days", "Maximum waste storage period (RD-03 Art.3)"),
        ("abstraction_cap_m3_per_year", r["abstraction_cap_m3_per_year"], "m3/yr", "Abstraction limit per well (RD-04 Art.3)"),
        ("pumping_test_above_m3_per_year", r["pumping_test_above_m3_per_year"], "m3/yr", "Pumping test required above (RD-05 Art.5)"),
        ("rfi_response_days", r["rfi_response_days"], "days", "Applicant response time for an RFI (RD-07 Art.8)"),
        ("high_abstraction_risk_m3_per_year", cfg["risk"]["high_abstraction_m3_per_year"], "m3/yr", "Risk factor high_abstraction (A-16)"),
    ]


# ------------------------------------------------------------------ manifest
def rules_manifest(docs: list[dict]) -> dict:
    seeded = [
        dict(seed_type="version_conflict_buffer", clause_refs=["RD-01 Art.6 v1", "RD-01 Art.6 v2"],
             detail="300 m (v1) -> 500 m (v2), cutover 2026-07-01", scenario="A3"),
        dict(seed_type="version_conflict_licence_validity", clause_refs=["RD-01 Art.4 v1", "RD-01 Art.4 v2"],
             detail="v2 adds: licence valid >= 90 days after application date", scenario="version_conflict"),
        dict(seed_type="conflicting_clauses_survey_age", clause_refs=["RD-05 Art.2 v1", "RD-01 Art.9 v2"],
             detail="24 months general vs 12 months for coastal sites (newest, most specific wins; surface conflict)",
             scenario="conflict_handling"),
        dict(seed_type="threshold_pm10", clause_refs=["RD-02 Art.3 v1"], detail="12.5 t/yr", scenario="threshold_not_met"),
        dict(seed_type="threshold_waste_storage", clause_refs=["RD-03 Art.3 v1"], detail="60 days", scenario="threshold_not_met"),
        dict(seed_type="threshold_abstraction", clause_refs=["RD-04 Art.3 v1"], detail="50,000 m3/yr", scenario="threshold_not_met"),
        dict(seed_type="ambiguous_wording", clause_refs=["RD-03 Art.5 v1"],
             detail="'adequate secondary containment', no numeric definition -> unclear", scenario="A5"),
        dict(seed_type="corpus_gap", clause_refs=[],
             detail="no clause on noise, desalination brine, data-centre cooling water, temporary (<30 days) "
                    "activities, solar farms", scenario="A5"),
    ]
    pending = sorted(f"{d['doc_id']} {d['version']}" for d in docs if "secondary" in d.get("languages_planned", []))
    return {"seeded_rules": seeded, "pending_secondary_language_docs": pending}


# ------------------------------------------------------------------ main
def main():
    args = C.base_arg_parser(__doc__.splitlines()[0]).parse_args()
    cfg = C.load_config(args.config)
    out = args.out / "rules"
    docs = load_sources()
    chunks, required_docs = [], None
    for doc in docs:
        stem = f"{doc['doc_id']}_{doc['version']}"
        for lang in C.languages(cfg):
            if lang not in doc["doc_title"]:
                continue  # translation not written yet
            v = view(doc, lang)
            suffix = "" if lang == cfg["languages"]["primary"] else f"_{lang}"
            pdf_path = out / "pdf" / f"{stem}{suffix}.pdf"
            render_pdf(v, lang, cfg, pdf_path)
            render_docx(v, lang, cfg, out / "docx" / f"{stem}{suffix}.docx")
            pages = clause_pages(pdf_path, v)
            for c in v["clauses"]:
                chunks.append(dict(
                    id=chunk_id(doc["doc_id"], doc["version"], c["article"], lang),
                    doc_id=doc["doc_id"], doc_title=v["title"], article=c["article"],
                    clause_id=c["clause_id"], clause_ref=f"{c['clause_id']} {doc['version']}",
                    title=c["title"], version=doc["version"],
                    effective_from=doc["effective_from"], effective_to=doc.get("effective_to"),
                    status=doc["status"], language=lang, permit_types=c["permit_types"],
                    text=c["text"], params=c["params"],
                    source_path=f"{SOURCE_ROOT}/{pdf_path.name}", page=pages[c["clause_id"]],
                ))
                if c["clause_id"] == "RD-07 Art.2":
                    required_docs = c["params"]["required_documents"]
    chunks.sort(key=lambda x: x["id"])
    C.write_jsonl(out / "chunks.jsonl", chunks)
    (out / "config_seed.sql").write_text(config_seed_sql(cfg, required_docs), encoding="utf-8")
    C.update_seeds_manifest(args.out, {"rules": rules_manifest(docs)})
    n_docs = len({d["doc_id"] for d in docs})
    print(f"documents {n_docs}, versions {len(docs)}, clause chunks {len(chunks)} -> {out}")


if __name__ == "__main__":
    main()
