---
tags: [projects/tm-assessment, ai/agents]
aliases: [UC1 assumptions]
created: 2026-11-25
---

# UC1 Assumption Log

> The brief says: where it is silent or ambiguous, state an assumption and proceed; recording assumptions is part of the assessment. Each entry gives the gap, the assumption, and what changes if it's wrong. **Open** = waiting on the assessment owner; **Working** = my decision, revisit if evidence changes.

Related: [[UC1 - Permit and NOC Processing Agent]] · [[UC1 Solution Architecture (Draft)]] · [[UC1 Security and Responsible AI Pack (Draft)]]

## Environment and delivery
| ID | Gap in the brief | Assumption | Impact if wrong | Status |
|---|---|---|---|---|
| A-01 | Sandbox provider (5.5) | Self-provisioned tenant: M365 Business trial + Azure subscription + Power Platform developer environment, all in one Entra tenant | Rebuild identity/connectors in the organiser tenant; IaC and solution export keep this cheap | Open |
| A-02 | Copilot Studio publishing licence | Build and test on the trial; publish to Teams via pay-as-you-go if it allows publishing; otherwise demo in the test pane and record publishing as a stated gap | FR-1.15 "Teams channel" partly shown | Open |
| A-03 | Duration (5.5) | Time-boxed build; UC1 first, UC2 reuses foundations | Scope of Should items | Open |
| A-04 | Dynamics 365 vs Dataverse (2.5, 3.9) | UC1 uses **custom Dataverse tables** for cases and decisions; no Dynamics 365 app. Developer environments can't install Dynamics 365 apps, and UC1 needs no D365-specific feature | None functionally; mention in the production roadmap | Working |
| A-05 | Single environment | One **dev** environment only; separate AI Search index per environment is implemented by naming (`rules-dev`) and parameterised IaC | Production roadmap describes test/prod | Working |

## Language, region, models
| ID | Gap | Assumption | Impact if wrong | Status |
|---|---|---|---|---|
| A-06 | Languages never named | **Working language: English. Secondary language: Arabic.** Chosen because the role sits in the Dubai office. Arabic means right-to-left text in letters, Arabic OCR, and an Arabic analyzer in AI Search | Swap analyzers, templates and test data; architecture unchanged | Open |
| A-07 | Secondary language Must or Should (5.5) | Treated as **Should** for UC1, as the brief marks BR-1.8, FR-1.6 (secondary), FR-1.20 | More multilingual test data and metrics | Open |
| A-08 | "Approved region" and residency | All data and model deployments in **one Azure region** that has every required service. If no single region has all of them, the exception is stated in the architecture document | Region change is an IaC parameter | Open |
| A-09 | Models | Azure OpenAI only. **Two tiers**: smaller chat model for classification checks and field normalisation, stronger chat model for compliance assessment and drafting, plus one embedding model. Exact versions fixed after checking region availability | Model choice in deliverable 5 | Working |
| A-10 | Fine-tuning | Out of scope per brief. Quality comes from prompting, retrieval and validation | n/a | Confirmed (brief) |

## Domain rules
| ID | Gap | Assumption | Impact if wrong | Status |
|---|---|---|---|---|
| A-11 | Departments (access tests A7) | Two departments: **Air and Industrial** (owns PT-1) and **Groundwater** (owns PT-2). Department is derived from permit type | Access matrix and test users | Working |
| A-12 | "Application date" for clause-in-force filter (FR-1.7) | Application date = **submission timestamp** of the pack. A clause is in force if `effective_from ≤ application_date` and (`effective_to` is null or `> application_date`) | Filter logic only | Working |
| A-13 | Checklist content | Checklists in [[UC1 Solution Architecture (Draft)#4. Permit checklists]], built from Appendix A.1 document list and the seeded defects | Checklist data, not code | Working |
| A-14 | Who issues letters | "Issue" = Power Automate sends the approved letter to a **test applicant mailbox** and stores it in SharePoint | None (portal is simulated) | Working |
| A-15 | Case record creation vs "Dataverse writes only after approval" (FR-1.1 vs 3.9) | **Intake API (deterministic code, not an agent)** creates the case record in Dataverse at submission. The "writes only after approval" rule applies to **decision and outcome writes**, which only Power Automate performs. Agent outputs (findings, drafts) go to an Azure SQL working store | If the owner reads it strictly, case creation moves to Power Automate triggered by the API | Working |
| A-16 | Risk tier factors (FR-1.13) | Explicit additive score: prior refusal, open enforcement case, site within protected-area buffer, any finding "not met", ≥ 2 findings "unclear", PT-2 abstraction volume above a fictional threshold, low-confidence key fields. Weights in a config table, shown to the officer | Tuning only | Working |
| A-17 | Routing by tier (BR-1.6) | Low/medium: technical reviewer approves. High: section head approves, with **mandatory reason** (automation-bias control from 3.14) | Approval flow branches | Working |
| A-18 | Protected areas for `buffer_check` | Stub: fixed list of synthetic protected-area points/polygons; distance via haversine; buffer = fictional 500 m | Stub only, per scope | Working |
| A-27 | Licence holder name vs applicant name (C3 validation) | Names match if equal after **normalisation**: Unicode NFKC, lower-case, legal suffixes unified (`L.L.C.`/`LLC`, `Co.`/`Company`, `Est.`/`Establishment`, `F.Z.E.`/`FZE`), punctuation removed, whitespace collapsed. A normalised-only match is **valid** but adds a low-severity officer note "holder name matched after normalisation". Rule lives in `config.thresholds` (`holder_name_normalised_match = 1`) and `config.yaml` `name_matching` | Stricter rule turns 3 clean packs into C3 invalid | Working |
| A-28 | Fairness variants and registry identity | A fairness variant is a separate applicant (new name and nationality) with a **cloned** site (same coordinates, land use, district; new site and plot ID) and a **cloned** trade licence (same type, dates and status; new number). Original and variant applicants have no prior decisions or open cases, so registry-derived risk factors are identical | Plot and licence numbers differ between the pair; graders must ignore ID fields | Working |

## Measurement definitions
| ID | Gap | Assumption | Status |
|---|---|---|---|
| A-19 | "Expert label" for completeness and classification | Labels are produced by the data generator (ground truth by construction) and spot-checked by hand. Limitation stated: no independent domain expert | Working |
| A-20 | Low-confidence threshold (FR-1.3) | Field flagged if Document Intelligence confidence < **0.80** or the normalised value fails validation; threshold tuned on a held-out split | Working |
| A-21 | "Accepted with no major edit" | Major edit = any finding's verdict changed, any finding added/removed, or > 20% of draft text changed (character-level diff) | Working |
| A-22 | Consistency target | Same pack run **5 times**; verdict = completeness verdict + each finding's met/not met/unclear + risk tier. Low temperature; structured outputs | Working |
| A-23 | Officer review time (45 → 15 min) | Measured in the scripted walkthrough: time from opening the case in the copilot to recorded decision, over the 8 acceptance scenarios. Baseline (45 min) taken from the brief, not measured | Working |
| A-24 | Second-RFI rate | Simulated: synthetic applicant resubmissions after the first RFI; second RFI needed if the resubmission still has a defect | Working |
| A-25 | Quote verification (3.9) | Quote must appear in the cited chunk after whitespace/Unicode normalisation (exact substring). No fuzzy match | Working |
| A-26 | Precedent relevance (FR-1.12) | Relevance judged by me on a sample of 20 cases. Limitation stated | Working |

## Proposed sizes for unsized labelled sets
| Set | Proposed size | Used for |
|---|---|---|
| Document-type classification set | All documents in the 60 packs | FR-1.2 (≥ 95%) |
| Unanswerable set | 30 questions/requirements with no answer in the corpus | FR-1.10 (≥ 90% abstention) |
| Consistency set | 20 packs × 5 runs | Same-verdict ≥ 95% |
| Fairness set | 20 pack pairs, identical facts, swapped applicant name/nationality | 3.11 fairness |
| Secondary-language subset | ≥ 15 packs containing Arabic or mixed documents | FR-1.20 |
| Resubmission set | 15 packs | Second-RFI rate |

## Data limitations (3.14)
- Synthetic packs are cleaner and more uniform than real submissions. Real scans, handwriting and template variety would lower extraction F1.
- Rule corpus is small (≥ 6 docs, ≥ 40 clauses); retrieval recall will be optimistic compared with a real corpus of hundreds of documents.
- All outputs are labelled **"Exercise content: fictional rules and data."**
