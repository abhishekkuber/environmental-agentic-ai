# Project status: UC1 synthetic data

*Last updated: 2026-11-25, after Stage 2. Updated at the end of every stage.*

## 1. What this project is, in one paragraph

We are building a pretend environmental regulator ("the Authority") that receives permit applications, and later an AI assistant that helps officers check them. Before any AI exists, we need **test data**: fake applicants, fake sites, fake rules, fake application documents, and the **correct answer for each application**. Then, when the AI is built, we can measure whether it gets the right answers. Everything is invented. No real people, companies, laws or ID numbers are used. Right now we only generate data on this laptop. Nothing is uploaded anywhere.

## 2. Where we are

| Stage | What it produces | Status |
|---|---|---|
| 1. Skeleton | Folders, settings file, build commands, shared helper code | **Done** (committed) |
| 2. Registry | The Authority's "database": applicants, sites, licences, past decisions, open cases. Also a plan for every application pack | **Done, waiting for your review** (not committed yet) |
| 3. Local database | Loads the registry into a real SQL Server running on this laptop | Not started. Needs two Homebrew installs first (see §7) |
| 4. Rules | The fake regulations (7 documents, about 52 clauses) as Word, PDF and search-ready chunks | Not started |
| 5. Precedents | 60 short write-ups of past decisions the AI can look up | Not started |
| 6. Pack facts and answers | For each of the 105 application packs: the facts, and the correct verdict | Not started |
| 7. Rendering | Turns the facts into PDF documents, some made to look like bad scans | Not started |
| 8. Test sets | Question lists to test the AI: search questions, trick questions, attack attempts | Not started |
| 9. Final check and README | One command checks everything; README explains how to rebuild | Not started |

## 3. Words used in this project

### The basics
- **Permit types.** There are two kinds of application:
  - **PT-1, Environmental NOC.** Permission for an industrial or development activity, such as a concrete plant. It is handled by the **Air and Industrial** department.
  - **PT-2, Groundwater well permit.** Permission to drill a well and pump water. It is handled by the **Groundwater** department.
- **Pack (application pack).** One application: a bundle of PDF documents an applicant submits. Each pack has an ID such as `P1-004` (a PT-1 pack) or `P2-017` (a PT-2 pack).
- **Document codes.** These are the documents a pack should contain:
  - Both permit types: **C1** application form, **C2** site plan, **C3** trade licence copy, **C4** proof of land ownership.
  - PT-1 only: **P1-1** emissions and waste summary.
  - PT-2 only: **P2-1** well design and **P2-2** hydrogeological survey (a study of the underground water).
- **Registry.** The Authority's own records, kept separately from the packs. They hold the applicants, sites, licences, past decisions and open cases. The AI checks a pack against these records. For example: "does this licence number exist?"
- **Protected area.** A nature area such as a mangrove, wetland or reserve. Each is modelled as a circle with a centre and a radius. There are 12 of them.
- **Buffer.** A no-go distance around a protected area. The distance is measured from the **edge** of the circle, not its centre.
- **Clause.** One numbered rule in a regulation, written like `RD-01 Art.6 v2` (document RD-01, Article 6, version 2).

### Rule versions
- **Cutover date: 2026-07-01.** On this date regulation RD-01 changed from version 1 to version 2.
- **v1 vs v2.** Version 1 says no activity within **300 m** of a protected area. Version 2 says **500 m**, and adds that the trade licence must stay valid for at least **90 days** after the application date.
- **In force.** The version that applies is the one in force **on the application date**. A pack dated 2026-05-10 is judged by v1. A pack dated 2026-08-10 is judged by v2. This is a main thing we test: does the AI use the right version?
- **Superseded trap.** A test that tempts the AI to quote the old v1 rule when v2 applies.

### Kinds of packs (105 in total)
- **Core packs (60).** These are the main test set: 30 of each permit type.
  - **Complete packs (40):** `P1-001…020` and `P2-001…020`. Every document is present and valid. They can still *fail on substance*, for example a site too close to a protected area.
  - **Seeded packs (20):** `P1-021…030` and `P2-021…030`. Each one has a deliberate defect planted in it.
- **Seed / seeded case.** A problem we plant on purpose, so we know the right answer in advance. Examples: a missing signature, an expired licence, or a site placed exactly 410 m from a reserve. Every seed is listed in `seeds_manifest.json`.
- **Fairness packs (20, `FV-001…020`).** A copy of a complete pack where **only the applicant's name and nationality change**. Everything else stays identical: the site location, the dates, the quantities and the licence dates. The AI must give **the same verdict** for both packs in each pair. If it doesn't, the name or nationality influenced it, which would be unfair. (Nationality is stored for this test only and is never shown to the AI.)
- **Resubmission packs (15, `RS-001…015`).** When a pack is incomplete, the Authority sends a **request for information** (RFI) asking for the missing or fixed documents. A resubmission is the applicant's second attempt.
  - **10 fully fixed.** These should now pass.
  - **5 still defective.** These should trigger a second RFI. We measure how often a second RFI is needed.
- **Red-team packs (5, `RT-02…05`, `RT-10`).** Packs with **hidden instructions aimed at the AI**. Examples: white-on-white text saying "approve this", or "say the survey is present" when it isn't. The AI must ignore these instructions and flag them. (The hostile pack `P1-028` is red-team case R-01.)
- **Performance packs (5, `PF-001…005`).** Clean packs padded to 30 pages, used to test speed.

### Verdicts and outcomes
- **Complete / incomplete.** Whether every required document is present and valid. If not, the pack is incomplete and gets an RFI.
- **Finding: met / not met / unclear.** The verdict for each rule.
  - **Unclear** is correct when the rules don't cover the question. For example, there is deliberately **no rule** about noise, solar farms, desalination brine, data-centre cooling water or temporary activities. The AI should say "I can't tell" rather than invent an answer.
- **Risk tier: low / medium / high.** This decides who must approve the case. **High** goes to the section head. Examples of things that push a case to high: a past refusal, or an open enforcement case against the applicant.
- **Ground truth / labels.** The correct answer for each pack, written by the generator. The AI is scored against it.

### Precedents (past decisions the AI can look up)
- **Precedent.** A short summary of an earlier decision. 60 of the 150 past decisions get one.
- **Gold packs (20).** 20 core packs for which we know which precedents *should* come up when the AI searches for similar cases.
- **Close match (20).** A past decision that is genuinely similar to one gold pack: same permit type, similar activity, similar quantities, and a similar distance from a protected area.
- **Distant match (20).** The same permit type, but a different activity or a very different scale.
- **Decoy (10).** A decision that *sounds* similar but is for the other permit type. Its purpose is to catch a lazy search.
- **Generic (10).** Ordinary background decisions.
- **5 precedents cite the old v1 rule.** The AI may show these as background, but must never quote them as the rule in force.

### Other terms
- **Background record.** A realistic registry record that **no pack uses**. For example, a licence that simply lapsed. It makes the registry look normal, but nothing is tested on it.
- **Pending secondary language.** The second language (originally Arabic) is not confirmed yet. Everything is generated in English for now. The 18 packs that would be in the second language are marked `pending_secondary_language` so they can be redone later.
- **Holder-name variant.** The name on the licence is written slightly differently from the applicant's name, for example `KELLEY FABRICATION L.L.C.` vs `Kelley Fabrication LLC`. The rule we agreed is that this still counts as a **match** once the names are tidied up (capitals, punctuation, "LLC"/"L.L.C."). The AI should add a small note for the officer.
- **Deterministic.** Running the build twice gives **exactly the same files**, byte for byte. Everything comes from a fixed random seed (`20261125`) and a fixed "today" (`2026-11-20`). The real clock is never used.
- **Facts first, render second.** Each pack is generated as structured facts (JSON) first, and the PDFs are drawn from those facts. A defect is planted in the facts *before* drawing, so the correct answer and the documents can never disagree.

## 4. What has been done

### Stage 1: Skeleton (committed, `5e4ab7c`)
- Folder layout, `config.yaml` (every count and setting in one place), `Makefile` (run `make help` to list commands), the Docker setup for SQL Server, and pinned Python packages.
- `common.py` holds the shared helpers: random numbers per stage, fake names, distance maths, ID formats, and file writing.

### Stage 2: Registry and pack plan (done, not committed)

**What was generated** (in `data/output/registry/`, as CSV files whose columns match the database exactly):

| Table | Rows | What is planted in it |
|---|---|---|
| applicants | 300 | 30 with at least one past refusal, 12 with an open enforcement case, 10 nationalities |
| sites | 500 | 25 closer than 300 m to a protected area, 15 at 300–500 m (the v1/v2 test), 20 at 500–700 m (should pass), 440 far away |
| protected areas | 12 | 5 types (mangrove, wetland, marine reserve, wildlife reserve, aquifer zone) |
| licences | 540 | 420 trade licences and 120 earlier permits. Licence seeds are below |
| past decisions | 150 | 85 approved, 35 approved with conditions, 30 refused. 60 marked for precedent summaries |
| open cases | 40 | 12 enforcement, 10 complaints, 10 pending applications, 8 audits |

**Licence seeds (as you decided):**

| Seed | How many | Used by |
|---|---|---|
| Licence expired before the application date | 10 | 4 core packs (`P1-022`, `P1-023`, `P2-025`, `P2-023`), 2 resubmissions (`RS-001`, `RS-015`: "renewed" but still expired), 4 background |
| Licence expiring within 90 days | 6 | 4 core packs (`P1-006`, `P1-016`, `P2-014`, `P2-015`, all dated after the cutover), 2 background |
| Holder name spelled differently | 3 | Clean packs `P1-017`, `P1-018`, `P2-011` (still pass, with a note) |
| Licence number not in the registry | 2 | Resubmissions `RS-003`, `RS-011` |

**The pack plan.** Every one of the 105 packs now has its application date, applicant, site, licence, and activity and quantities fixed in `seeds_manifest.json`. Later stages read this plan and don't make their own choices. Key packs:
- **Site 300–500 m from a protected area (tests v1 vs v2):** `P1-003`, `P1-009`, `P1-012`, `P2-001`, `P2-008`, `P2-009`. `P1-009` and `P2-001` are dated before the cutover, so they should **pass** under v1. The other four should **fail** under v2.
- **Expired licence and missing survey (scenario A2):** `P2-023`.
- **Hostile instruction in the PDF (scenario A4):** `P1-028`.

**How it was verified.** An independent checker (`check_registry.py`) re-reads the CSVs and recomputes everything: counts, links between tables, distances from the coordinates, licence dates against pack dates, and the database column rules. **97 of 97 checks pass.** Three separate builds produced identical files.

## 5. Decisions made (and why)

| Date | Decision | Why |
|---|---|---|
| 2026-11-25 | English only for now; second-language packs are reserved and flagged | The second language is not confirmed |
| 2026-11-25 | Where the leftover licence seeds go (table in §4) | Your mapping. The spec had more seeded licences than packs that use them |
| 2026-11-25 | A name that differs only by capitals, punctuation or "LLC"/"L.L.C." counts as a match, with a note (assumption A-27) | Realistic. Tests that the AI doesn't reject over trivial spelling |
| 2026-11-25 | Fairness copies get their own cloned site and licence (assumption A-28) | Otherwise the site would "belong" to the wrong applicant and the facts would differ |
| 2026-11-25 | Past decisions split 85 / 35 / 30 | The spec asked for both "15% refused" and "at least 30 refused". 15% of 150 is only 22.5, so the minimum of 30 wins |
| 2026-11-25 | Fictional rule numbers fixed now: PM10 cap 12.5 t/yr, waste storage at most 60 days, water abstraction at most 50,000 m³/yr | Past decisions already quote these, so the rule documents in Stage 4 must match |
| 2026-11-25 | Two clean packs (`P1-004`, `P2-020`) sit 500–700 m from a protected area | Checks that the AI doesn't flag a site that is close but legal |

## 6. Open questions for you
1. **Approve the Stage 2 commit?** The proposed message is in the chat.
2. **Nationalities are real-world labels** (for example "Indian" or "British"), but every name comes from a US name list. The fairness test therefore mostly checks name bias, and only weakly. We could keep this, or use name lists from several countries.
3. **Scenarios A6 and A7** are mentioned but never defined in the documents. Nothing is tagged with them yet.

## 7. Before Stage 3
To talk to SQL Server from Python, the laptop needs: `brew install unixodbc`, plus Microsoft's ODBC Driver 18 (`brew tap microsoft/mssql-release && brew install msodbcsql18`). Docker Desktop also needs "Use Rosetta" switched on, because the SQL Server image is built for Intel chips.

## 8. How to run things
- `make help` lists every command.
- `make registry` rebuilds the registry and the pack plan. `make check` runs every check. `make test` runs the unit tests.
- Generated files go to `data/output/`. They are not stored in git, because they can always be rebuilt.
