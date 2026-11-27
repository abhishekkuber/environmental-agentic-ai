#!/usr/bin/env python
"""Stage 8: decision-pack and RFI-letter templates (brief Appendix A.1), worked examples.

Templates live in render/templates/{lang}/decision_pack.html and rfi_letter.html (English now;
a secondary-language folder is added once the language is confirmed). This script fills them
from the labels and facts and writes to <out>/templates/examples/:
  {name}.pdf    rendered with WeasyPrint
  {name}.docx   the same content as a Word file (fixed timestamps, byte-identical builds)
  {name}.json   the filled context, so checks can compare it with the label
Examples: decision pack P1-002 (A1), RFI letter P2-023 (A2), decision pack P1-001 (A6, high tier).
Exercise content: fictional data.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import docx
from docx.shared import Pt
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from weasyprint import HTML

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
from gen_rules import fixed_zip  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent / "render" / "templates"
FOOTER = "Draft: requires officer approval. Exercise content: fictional data."
EXAMPLES = [  # (file name, template, pack, scenario)
    ("decision_pack_P1-002", "decision_pack", "P1-002", "A1"),
    ("rfi_letter_P2-023", "rfi_letter", "P2-023", "A2"),
    ("decision_pack_P1-001", "decision_pack", "P1-001", "A6"),
]
DRAFT_OFFSET_DAYS = 2   # drafts are dated two days after the application (deterministic, no clock)

DEPARTMENT = {"air_industrial": "Air and Industrial Department", "groundwater": "Groundwater Department"}
PERMIT_NAME = {"PT-1": "Environmental NOC", "PT-2": "Groundwater well permit"}
REQUIREMENT = {
    "buffer_distance": "Distance to protected area", "licence_validity": "Trade licence validity",
    "pm10_cap": "PM10 emissions", "waste_storage": "Waste storage period",
    "secondary_containment": "Secondary containment for liquid waste", "abstraction_cap": "Annual abstraction",
    "well_design": "Well design", "survey_age": "Age of hydrogeological survey", "pumping_test": "Pumping test",
}
VERDICT = {"met": "Met", "not_met": "Not met", "unclear": "Unclear"}
RECOMMENDATION = {"approve": "Approve", "approve_with_conditions": "Approve with conditions", "refuse": "Refuse",
                  "request_information": "Request information", None: "No recommendation"}
APPROVER = {"technical_reviewer": "Technical reviewer", "section_head": "Section head"}


def requirement_label(req: str) -> str:
    if req.startswith("activity_not_covered:"):
        return "Activity not covered by the rules (" + req.split(":", 1)[1].replace("_", " ") + ")"
    return REQUIREMENT.get(req, req.replace("_", " ").capitalize())


def doc_files(facts: dict) -> dict[str, str]:
    """Document code -> uploaded file name (from what the portal sent)."""
    return {x["declared_type"]: x["filename"] for x in facts["portal_payload"]["documents"]}


def base_context(cfg: dict, lab: dict, facts: dict) -> dict:
    c1 = next(d for d in lab["documents"] if d["true_type"] == "C1")["fields"]
    draft = C.to_date(lab["application_date"]) + timedelta(days=DRAFT_OFFSET_DAYS)
    return dict(
        case_id=lab["case_id"], pack_id=lab["pack_id"], permit_type=lab["permit_type"],
        permit_name=PERMIT_NAME[lab["permit_type"]], department_name=DEPARTMENT[lab["department"]],
        applicant_name=facts["applicant"]["name"], contact_email=facts["applicant"]["contact_email"],
        site_address=facts["site"]["address"], plot_id=facts["site"]["plot_id"], land_use=facts["site"]["land_use"],
        activity=c1.get("activity_description", ""), application_date=lab["application_date"],
        draft_date=C.iso(draft), letter_date=C.iso(draft), language=lab["language"],
    )


def decision_context(cfg: dict, lab: dict, facts: dict, chunks: dict, gold: dict, precs: dict) -> dict:
    c = base_context(cfg, lab, facts)
    files = doc_files(facts)
    names = cfg["document_names"]
    findings = []
    for f in lab["expected_findings"]:
        ev = f["evidence"]
        findings.append(dict(
            requirement=f["requirement"], requirement_label=requirement_label(f["requirement"]),
            verdict=f["verdict"], verdict_label=VERDICT[f["verdict"]], clause=f["clause"],
            quote=chunks[f["clause"]]["text"] if f["clause"] else "No clause in force covers this activity.",
            rationale=f["rationale"],
            evidence=f"{names[ev['doc']]} ({ev['doc']}, {files.get(ev['doc'], 'n/a')}): " + ", ".join(ev["fields"]),
        ))
    n = {v: sum(f["verdict"] == v for f in findings) for v in VERDICT}
    rec = lab["expected_recommendation"]
    summary = (f"The application is {lab['expected_verdict']}. {len(findings)} requirements were assessed under the "
               f"rules in force on {lab['application_date']}: {n['met']} met, {n['not_met']} not met, "
               f"{n['unclear']} unclear.")
    reason = {"approve": "All assessed requirements are met.",
              "approve_with_conditions": "All requirements that can be tested are met; the conditions in section 3 apply.",
              "refuse": "At least one requirement is not met (see section 2).",
              "request_information": "The application is incomplete.",
              None: "Suspected injected instructions were found in a document; the case is routed to an officer."}[rec]
    if n["unclear"]:
        reason += " Unclear findings need the officer's judgement before approval."
    opens = [f"{requirement_label(f['requirement'])}: {f['officer_note'] or f['rationale']}"
             for f in lab["expected_findings"] if f["verdict"] == "unclear"]
    opens += lab["officer_notes"] + lab["not_assessed"]
    factors = cfg["risk"]["factors"]
    g = gold.get(lab["pack_id"])
    c.update(
        summary=summary, findings=findings, conditions=lab["proposed_conditions"],
        recommendation=rec, recommendation_label=RECOMMENDATION[rec], recommendation_reason=reason,
        open_questions=opens, risk_tier=lab["expected_risk_tier"], risk_score=lab["expected_risk_score"],
        approver=lab["expected_route_to_role"], approver_label=APPROVER[lab["expected_route_to_role"]],
        reason_required=lab["expected_route_to_role"] == "section_head",
        risk_factors=[dict(factor=k, description=factors[k]["description"], weight=factors[k]["weight"])
                      for k in lab["expected_risk_factors"]],
        precedents=[dict(id=p, decision_date=precs[p]["decision_date"],
                         outcome=precs[p]["outcome"].replace("_", " "), activity=precs[p]["activity"])
                    for p in (g["expected_top3"] if g else [])],
    )
    return c


def rfi_context(cfg: dict, lab: dict, facts: dict, chunks: dict) -> dict:
    c = base_context(cfg, lab, facts)
    names = cfg["document_names"]
    items = []
    for code, status in lab["completeness"].items():
        if status == "present":
            continue
        reason = lab["completeness_reasons"][code]
        doc = names[code]
        needed = (f"Please submit the {doc.lower()}." if status == "missing"
                  else f"Please submit a corrected or current {doc.lower()}.")
        items.append(dict(code=code, document=doc, status=status, found=reason[:1].upper() + reason[1:],
                          needed=needed))
    days = cfg["rules"]["rfi_response_days"]
    rfi_ref = cfg["rules"]["clause_refs"]["rfi"]
    c.update(rfi_items=items, response_days=days,
             deadline=C.iso(C.to_date(c["letter_date"]) + timedelta(days=days)),
             rfi_clause=rfi_ref, rfi_quote=chunks[rfi_ref]["text"])
    return c


# ------------------------------------------------------------------ writers
def render_pdf(template: str, ctx: dict, lang: str) -> bytes:
    env = Environment(loader=FileSystemLoader(TEMPLATES / lang), undefined=StrictUndefined, autoescape=True)
    html = env.get_template(f"{template}.html").render(c=ctx, lang=lang, footer=FOOTER)
    return HTML(string=html, base_url=str(TEMPLATES / lang)).write_pdf()


def _table(d, header: list[str], rows: list[list[str]]):
    t = d.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    for cell, h in zip(t.rows[0].cells, header):
        cell.text = h
        cell.paragraphs[0].runs[0].bold = True
    for r in rows:
        for cell, v in zip(t.add_row().cells, r):
            cell.text = str(v)


def render_docx(template: str, ctx: dict, cfg: dict) -> bytes:
    ref = C.to_date(ctx["draft_date"])
    d = docx.Document()
    cp = d.core_properties
    cp.author = cp.last_modified_by = "The Authority (fictional)"
    cp.created = cp.modified = cp.last_printed = datetime(ref.year, ref.month, ref.day)
    cp.revision = 1
    cp.keywords = "Exercise content: fictional data"
    d.styles["Normal"].font.name = "Arial"
    d.styles["Normal"].font.size = Pt(10)
    d.add_paragraph(f"THE AUTHORITY (FICTIONAL ENVIRONMENTAL REGULATOR) · {ctx['department_name']}")
    if template == "decision_pack":
        cp.title = f"Decision pack (draft) {ctx['case_id']}"
        d.add_heading("Decision pack (draft)", level=0)
        d.add_paragraph("Draft for officer review. This is not a decision. Nothing has been issued to the applicant.").runs[0].bold = True
        _table(d, ["Field", "Value"], [
            ["Case", f"{ctx['case_id']} (pack {ctx['pack_id']})"], ["Permit type", f"{ctx['permit_type']} {ctx['permit_name']}"],
            ["Applicant", ctx["applicant_name"]], ["Site", f"{ctx['site_address']} (plot {ctx['plot_id']}, {ctx['land_use']})"],
            ["Activity", ctx["activity"]], ["Application date", ctx["application_date"]], ["Draft prepared", ctx["draft_date"]]])
        d.add_heading("1. Summary", level=2)
        d.add_paragraph(ctx["summary"])
        d.add_heading("2. Findings", level=2)
        _table(d, ["Requirement", "Verdict", "Clause", "Quoted passage", "Evidence"],
               [[f["requirement_label"], f["verdict_label"], f["clause"] or "None", f"\"{f['quote']}\" {f['rationale']}",
                 f["evidence"]] for f in ctx["findings"]])
        d.add_heading("3. Proposed conditions", level=2)
        for x in ctx["conditions"] or ["None proposed."]:
            d.add_paragraph(x, style="List Number" if ctx["conditions"] else None)
        d.add_heading("4. Recommendation", level=2)
        d.add_paragraph(f"{ctx['recommendation_label']}. {ctx['recommendation_reason']}")
        d.add_heading("5. Open questions for the officer", level=2)
        for x in ctx["open_questions"] or ["None."]:
            d.add_paragraph(x, style="List Bullet" if ctx["open_questions"] else None)
        d.add_heading("6. Risk tier and factors", level=2)
        d.add_paragraph(f"Risk tier {ctx['risk_tier']} (score {ctx['risk_score']}). Approver: {ctx['approver_label']}"
                        + ("; a written reason is required (BR-1.6)." if ctx["reason_required"] else "."))
        if ctx["risk_factors"]:
            _table(d, ["Factor", "Weight"], [[r["description"], r["weight"]] for r in ctx["risk_factors"]])
        if ctx["precedents"]:
            d.add_heading("7. Similar past decisions (context only, not rules)", level=2)
            _table(d, ["Precedent", "Decided", "Outcome", "Activity"],
                   [[p["id"], p["decision_date"], p["outcome"], p["activity"]] for p in ctx["precedents"]])
    else:
        cp.title = f"Request for information (draft) {ctx['case_id']}"
        d.add_paragraph("Draft: requires officer approval before it is sent.").runs[0].bold = True
        d.add_paragraph(f"{ctx['applicant_name']}\n{ctx['site_address']}\n{ctx['contact_email']}")
        d.add_paragraph(f"Date: {ctx['letter_date']}\nOur reference: {ctx['case_id']}\n"
                        f"Application: {ctx['permit_type']} {ctx['permit_name']}, submitted {ctx['application_date']}")
        d.add_heading("Request for information", level=1)
        d.add_paragraph(f"Dear {ctx['applicant_name']},")
        d.add_paragraph("Thank you for your application. We have checked the documents you submitted against the "
                        "required documents for this permit type. The application is incomplete, so we cannot assess "
                        "it yet. Please provide the following:")
        _table(d, ["#", "Document", "What we found", "What we need from you"],
               [[i + 1, f"{it['document']} ({it['code']})", it["found"], it["needed"]] for i, it in enumerate(ctx["rfi_items"])])
        d.add_paragraph(f"Please respond by {ctx['deadline']} ({ctx['response_days']} days from the date of this letter).").runs[0].bold = True
        d.add_paragraph(f"This request is made under {ctx['rfi_clause']}: \"{ctx['rfi_quote']}\"")
        d.add_paragraph("When we receive the documents, we will continue with the assessment of your application. "
                        f"If you have questions, reply to this letter quoting reference {ctx['case_id']}.")
        d.add_paragraph(f"Yours sincerely,\n\n[Name of approving officer]\n{ctx['department_name']}, the Authority")
    sec = d.sections[0]
    sec.header.paragraphs[0].text = f"{cp.title} · DRAFT"
    sec.footer.paragraphs[0].text = FOOTER
    buf = io.BytesIO()
    d.save(buf)
    return fixed_zip(buf.getvalue(), (ref.year, ref.month, ref.day, 0, 0, 0))


def main():
    args = C.base_arg_parser("Stage 8: decision-pack and RFI-letter template examples").parse_args()
    cfg = C.load_config(args.config)
    out = args.out
    lang = cfg["languages"]["primary"]
    chunks = {c["clause_ref"]: c for c in map(json.loads, (out / "rules" / "chunks.jsonl").read_text().splitlines())
              if c["language"] == lang}
    gold = {g["pack_id"]: g for g in map(json.loads, (out / "eval" / "precedents_gold.jsonl").read_text().splitlines())}
    precs = {p["id"]: p for p in map(json.loads, (out / "precedents" / "precedents.jsonl").read_text().splitlines())}
    dest = out / "templates" / "examples"
    dest.mkdir(parents=True, exist_ok=True)
    for name, template, pid, scenario in EXAMPLES:
        lab = C.read_json(out / "labels" / f"{pid}.json")
        facts = C.read_json(out / "packs" / pid / "facts.json")
        ctx = (decision_context(cfg, lab, facts, chunks, gold, precs) if template == "decision_pack"
               else rfi_context(cfg, lab, facts, chunks))
        ctx.update(template=template, scenario=scenario, footer=FOOTER)
        (dest / f"{name}.pdf").write_bytes(render_pdf(template, ctx, lang))
        (dest / f"{name}.docx").write_bytes(render_docx(template, ctx, cfg))
        C.write_json(dest / f"{name}.json", ctx)
        print(f"{name}: pdf, docx, json")


if __name__ == "__main__":
    main()
