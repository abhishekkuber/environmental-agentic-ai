# Project status: UC1 synthetic data

*Last updated: 2026-11-25, after Stage 4. Updated at the end of every stage.*

## 1. What this project is, in one paragraph

We are building a pretend environmental regulator ("the Authority") that receives permit applications, and later an AI assistant that helps officers check them. Before any AI exists, we need **test data**: fake applicants, fake sites, fake rules, fake application documents, and the **correct answer for each application**. Then, when the AI is built, we can measure whether it gets the right answers. Everything is invented. No real people, companies, laws or ID numbers are used. Right now we only generate data on this laptop. Nothing is uploaded anywhere.

## 2. Where we are

| Stage | What it produces | Status |
|---|---|---|
| 1. Skeleton | Folders, settings file, build commands, shared helper code | **Done** (committed) |
| 2. Registry | The Authority's "database": applicants, sites, licences, past decisions, open cases. Also a plan for every application pack | **Done** (committed, `49cc2d4`) |
| 3. Local database | Loads the registry into a real SQL Server running on this laptop | **Done** (committed) |
| 4. Rules | The fake regulations (7 documents, 8 versions, 60 clauses) as Word, PDF and search-ready chunks, plus the settings tables (checklists, risk weights, thresholds) | **Done** (committed) |
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

### Stage 2: Registry and pack plan (committed, `49cc2d4`)

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

### Stage 3: Local database (committed)

**What it does.** `make db-up` starts SQL Server 2022 in Docker. `make db-load` then does four things:
1. Wipes and recreates a database called `uc1`, so it is safe to run again.
2. Applies `docs/schema.sql` exactly as written. The file itself is not modified.
3. Loads the six registry CSVs.
4. Checks the result.

**What was checked (17 of 17 pass):**
- Row counts in the database equal the CSV row counts for all six tables.
- All 4 read-only **views** return rows, and their counts agree with the planted data: 30 applicants with a refusal, 12 with open enforcement.
- **Data masking works.** A database user without the "UNMASK" permission sees `rXXX@XXXX.com` for an email, `XXXX8769` for an ID number and `xxxx` for a phone number. The administrator sees the real (fake) values.
- Special characters (for example "m³") and the 6-decimal coordinates survive the load unchanged.

**New words:**
- **View.** A saved query that shows only some columns of a table. The AI will read the registry only through views. For example, `v_applicant_history` shows refusal and enforcement counts but never the nationality or ID number.
- **Data masking.** SQL Server hides sensitive columns (email, ID number, phone) from anyone without the UNMASK permission. No AI identity will ever be given UNMASK.

**Setup problem we hit (fixed).** Microsoft's database driver looks for OpenSSL version 3 or older, but Homebrew now installs version 4 as its default. The `make db-load` command now points the driver at OpenSSL 3, which you already have, and this applies to that command only. Nothing on the laptop was changed.

### Stage 4: Rules (committed)

**What was written.** Seven fake regulations, hand-written as YAML files in `data/rules_src/` (one file per version):

| Doc | Title | Applies to | Versions | Clauses |
|---|---|---|---|---|
| RD-01 | Environmental NOC Regulation | both (mostly PT-1) | v1 (2023-01-01 to 2026-06-30, superseded) and v2 (from 2026-07-01, current) | 10 + 10 |
| RD-02 | Air Emissions Technical Standard | PT-1 | v1 | 7 |
| RD-03 | Waste Storage and Handling Guideline | PT-1 | v1 | 6 |
| RD-04 | Groundwater Well Drilling Regulation | PT-2 | v1 | 8 |
| RD-05 | Hydrogeological Survey Technical Standard | PT-2 | v1 | 6 |
| RD-06 | Protected Areas and Buffer Zones Guideline | both | v1 | 5 |
| RD-07 | General Application Requirements | both | v1 | 8 |

**What `make rules` produces** (in `data/output/rules/`):
- **A Word and a PDF file per version** (8 of each). Each has the footer "Exercise content: fictional data." on every page, and v1 of RD-01 carries a red "Superseded" banner.
- **`chunks.jsonl`.** One record per clause: 60 in total. This is the file the AI's search index will be built from later. Each record holds the clause text plus its document, article, version, in-force dates, status, language, permit types, and the PDF page it appears on.
- **`config_seed.sql`.** It fills the database's settings tables:
  - the document checklist per permit type, with 11 items copied from RD-07 Art. 2
  - the 7 risk factors and their weights
  - the 3 risk tiers
  - 14 thresholds
  
  `make db-load` now loads it too.

**The planted rule cases** (the spec's "seeded rules"):
- **Buffer changes between versions.** RD-01 Art. 6 says 300 m in v1 and 500 m in v2.
- **Licence rule is new in v2.** RD-01 Art. 4 v2 adds "the trade licence must stay valid at least 90 days after the application date". v1 only says "hold a trade licence", so v1 is a trap.
- **Two clauses disagree on survey age.** RD-05 Art. 2 says the survey must be at most 24 months old. RD-01 Art. 9 v2 says at most 12 months for coastal sites. The newer, more specific rule wins, and the AI should point out the conflict.
- **Numeric limits:**
  - PM10 dust at most 12.5 t/yr (RD-02 Art. 3)
  - waste stored at most 60 days (RD-03 Art. 3)
  - water pumped at most 50,000 m³/yr per well (RD-04 Art. 3)
- **Deliberately vague.** RD-03 Art. 5 says "adequate secondary containment" with no number anywhere, so the right verdict is "unclear".
- **Deliberate gaps.** No clause mentions noise, desalination brine, data-centre cooling water, temporary activities or solar farms. The checker scans every clause to make sure.

**How it was verified.** An independent checker (`check_rules.py`) runs **48 of 48 checks, all passing**. Among them:
- every threshold the packs will be tested against appears in a clause, both as a number and in the text
- for every day from 2023 to 2027, exactly one version of each RD-01 article is in force
- every clause cited by the 150 past decisions exists and was in force on the decision date
- the checklist in the database matches RD-07 Art. 2
- every clause's text really appears in the PDF on the page recorded for it
- two separate builds are byte-identical

The database load now passes 21 of 21 checks, including the 4 settings tables.

**New words:**
- **Clause ID vs clause reference.** `RD-01 Art.6` is the clause ID, which stays the same across versions. `RD-01 Art.6 v2` is the clause reference, meaning that particular version. Findings and past decisions cite the reference, so you can always tell which version was used.
- **Chunk.** One clause cut out as a separate record, ready for a search index. The AI searches chunks, not whole documents.
- **Exclusive end date.** RD-01 v1 is stored with `effective_to = 2026-07-01`, meaning "in force up to but not including 1 July". The document text says "until 30 June 2026". See the decision below for why.
- **Risk score and tier.** Each risk factor adds points, for example a past refusal adds 3 and a rule not met adds 2. The total picks the tier: 0 is low, 1–2 is medium, 3 or more is high. High-tier cases must be approved by the section head.
- **Secondary-language translations.** The spec wants RD-01, RD-04 and RD-07 in the second language as well. These are listed as pending in `seeds_manifest.json`.

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
| 2026-11-25 | RD-01 v1 stored as ending on 2026-07-01 (exclusive), not 2026-06-30 | The in-force rule (A-12) treats the end date as "strictly after". With 06-30 stored, no version of RD-01 would apply on 30 June itself. The document text still says "until 30 June 2026" |
| 2026-11-25 | Clauses are cited as `RD-01 Art.6 v2` (with version); `clause_id` stays version-free | Matches the database, which stores clause ID and version separately, and the label example in the spec |
| 2026-11-25 | Risk weights: refusal 3, open enforcement 3, inside buffer 2, a rule not met 2, two or more unclear 1, PT-2 pumping above 40,000 m³/yr 1, low-confidence fields 1. Tiers: low 0, medium 1–2, high 3 or more | Turns assumption A-16 into numbers. It gives the spec's expected tiers: clean packs low or medium, and packs from applicants with a past refusal or open enforcement high |
| 2026-11-25 | A few extra concrete rules were added so every clause can be tested (for example, a pumping test is needed above 20,000 m³/yr, and casing must be grouted to 6 m) | The spec asks for one testable requirement per clause. Stage 6 must make the packs follow them |

## 6. Open questions for you
1. **Nationalities are real-world labels** (for example "Indian" or "British"), but every name comes from a US name list. The fairness test therefore mostly checks name bias, and only weakly. We could keep this, or use name lists from several countries.
2. **Scenarios A6 and A7** are mentioned but never defined in the documents. Nothing is tagged with them yet.
3. **RD-01 v1 end date.** Is it OK to store it as 2026-07-01 (exclusive), as described in §5? The alternative is changing the in-force rule in A-12 to "on or after".

## 7. Machine setup (done)
These are already done on this laptop:
- `brew install unixodbc`, plus Microsoft's ODBC Driver 18 (`brew tap microsoft/mssql-release && brew install msodbcsql18`).
- "Use Rosetta" switched on in Docker Desktop, because the SQL Server image is built for Intel chips.
- `.env` created with a random database password. It is not stored in git.

On a fresh machine, repeat these steps first.

## 8. How to run things

Run every command from the project folder in a terminal:
```bash
cd ~/Desktop/Upskilling/agentic-ai-techm
```
`make help` lists every command at any time.

### A. First time on a machine (once only)
1. Do the machine setup in §7.
2. Create the Python environment and install packages:
   ```bash
   make venv
   ```

### B. Rebuild the data (no Docker needed)
```bash
make registry     # registry CSVs + pack plan into data/output/
make rules        # rule documents (Word, PDF), chunks.jsonl, config_seed.sql
make check        # all checks: registry "97/97 passed", rules "48/48 passed"
make test         # run the unit tests
```
Run `make registry` before `make rules`. The rules checker also confirms every clause the past decisions cite.
To read the rules, open the PDFs in `data/output/rules/pdf/` (for example `RD-01_v1.pdf` and `RD-01_v2.pdf` side by side).
Generated files go to `data/output/`. They are not stored in git, because these commands can always rebuild them, byte for byte.

### C. Start the database and load the data
1. **Start Docker Desktop.** Open it from Applications, or run `open -a Docker`. Wait until the whale icon in the menu bar stops moving, which takes about 30 seconds. Check it is ready:
   ```bash
   docker info --format '{{.ServerVersion}}'    # prints a version number when ready
   ```
2. **Start SQL Server** (the container is called `uc1-mssql`):
   ```bash
   make db-up
   ```
3. **Wait for it to finish booting.** Under Rosetta this takes about 30–90 seconds. You can watch for the line "Recovery is complete":
   ```bash
   docker logs -f uc1-mssql      # press Ctrl+C to stop watching
   ```
   You can also skip this step, because `make db-load` keeps retrying for up to 4 minutes.
4. **Load the registry and check it:**
   ```bash
   make db-load      # should end with "21/21 passed"
   ```
   This also fills the settings tables (checklists, risk weights, thresholds) when `make rules` has been run. Without it you get 17 checks.
   This wipes and rebuilds the `uc1` database each time, so it is always safe to rerun. Any edits made by hand inside the database are lost.

Is it running? `docker ps` should list `uc1-mssql` with status "Up".

### D. Look at the data
**In VS Code:**
1. Install the **"SQL Server (mssql)"** extension by Microsoft.
2. Click its icon in the sidebar, then **Add Connection**, and fill in:
   - Server `localhost,1433`
   - SQL Login, user `sa`
   - Password from `.env` (`grep MSSQL_SA_PASSWORD .env` prints it)
   - Database `uc1`
   - **Trust server certificate: Yes**
3. Browse **Tables / Views**, or open a query window.

**In the terminal:**
```bash
set -a; source .env; set +a
docker exec -it uc1-mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -d uc1
```
Type a query, then `GO` on its own line to run it, and `exit` to leave. Example:
```sql
SELECT TOP 10 * FROM registry.v_applicant_history WHERE prior_refusals > 0;
GO
```

### E. Stop everything
```bash
make db-down      # stops and removes the container; the data is kept in a Docker volume
```
Then quit Docker Desktop if you like. The next time, start again from **C.1**. You don't need to run `make db-load` again unless the data changed.

To delete the database completely and start from zero, run `docker compose down -v`. The `-v` also deletes the stored data. After that, run `make db-up` and `make db-load` again.

### F. If something goes wrong
| Symptom | Likely cause and fix |
|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop isn't running. Start it (C.1) |
| `make db-load` says "SQL Server not reachable" | The container is still booting or stopped. Check `docker ps` and `docker logs uc1-mssql`, then rerun |
| `OpenSSL library could not be loaded` | You ran the loader directly instead of through `make db-load`. Use `make db-load`, which points the driver at OpenSSL 3 |
| `MSSQL_SA_PASSWORD missing` | `.env` doesn't exist. See §7 |
| `Login failed for user 'sa'` | The password in `.env` changed after the database was first created. Run `docker compose down -v`, then `make db-up` and `make db-load` |
| The container stops right after starting | Rosetta is off in Docker Desktop (§7), or the password is too weak |
