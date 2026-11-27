# UC1 synthetic data

All data for Use Case 1 (Permit and NOC Processing) is **fictional** and generated locally, with no AI calls. Every rendered document carries the footer *"Exercise content: fictional data."* The reasons behind the data (what it tests and why it looks the way it does) are in [`docs/UC1 Data Preparation.md`](../docs/UC1%20Data%20Preparation.md).

## Settings

| Setting | Value | Where |
|---|---|---|
| Global seed | `20261125` | `config.yaml` (`seed`); every generator also takes `--seed` |
| Seed per stage | global seed + offset (registry 100, rules 200, precedents 300, packs 400, render 500, eval 600) | `config.yaml` (`stage_seed_offsets`) |
| Reference date ("today") | `2026-11-20` | `config.yaml`; the system clock is never read |
| Rule cutover (RD-01 v1 → v2) | `2026-07-01` | `config.yaml` |
| Application dates | 2026-03-01 to 2026-11-20 | `config.yaml` |
| Language | English only; second language deferred (`secondary_language: null`) | `config.yaml` |
| Faker locale | `en_US` | `config.yaml` |

## Rebuild

```bash
make venv          # once: Python 3.11 virtual environment + requirements.txt
make all           # registry → rules → precedents → packs → render → eval → templates → check (about 40 s)
make test          # unit tests, including byte-identical rebuild tests
```

Each stage also has its own target (`make registry`, `make rules`, …, `make templates`). The stages must run in that order, because each one reads the output of the one before. `make check` runs every checker and finishes with a spec-target-vs-actual table, also saved as `data/output/summary.md`.

**Reproducibility.** Running `make all` in a clean clone with the same seed gives byte-identical output (verified 2026-11-27: all 934 generated files have the same SHA-256 hashes).

To load the registry into a local SQL Server 2022 (Docker), run `make db-up`, then `make db-load`. See `STATUS.md` §8 for the full guide.

## What is generated (`data/output/`, git-ignored)

| Path | Contents | Count |
|---|---|---|
| `registry/*.csv` | applicants, sites, protected_areas, licences, prior_decisions, open_cases (columns match `docs/schema.sql`) | 300 / 500 / 12 / 540 / 150 / 40 |
| `rules/docx`, `rules/pdf` | 7 rule documents in 8 versions (RD-01 v1 and v2) | 8 + 8 files |
| `rules/chunks.jsonl` | one record per clause version, with index metadata | 60 |
| `rules/config_seed.sql` | checklist items, risk factors, risk tiers, thresholds | 4 tables |
| `precedents/precedents.jsonl` | precedent summaries (context only, never rules) | 60 |
| `packs/{pack_id}/` | `facts.json`, the rendered PDFs, `render_manifest.json` | 105 packs, 574 PDFs, 1,283 pages |
| `labels/{pack_id}.json` | correct answer per pack | 105 |
| `eval/` | retrieval (150), unanswerable (30), trajectories (30), red team (21), consistency (20 × 5), fairness pairs (20), precedents gold (20), `index.yaml` | |
| `templates/examples/` | decision pack (P1-002, P1-001) and RFI letter (P2-023), each as PDF, DOCX and JSON | 3 |
| `seeds_manifest.json` | every seeded record and evaluation item, with its seed type and scenario | |
| `summary.md` | spec target vs actual | 53 rows |

## Seeded cases

**Registry** (see `seeds_manifest.json` → `registry_seeds`, `background`)
- **Applicants:** 30 with at least one refusal; 12 with an open enforcement case.
- **Sites by distance to the nearest protected-area edge:** 25 under 300 m, 15 at 300–500 m, 20 at 500–700 m, 440 beyond.
- **Licences:**
  - 10 expired before their pack's date: 6 linked to packs, 4 background.
  - 6 expiring within 90 days: 4 linked, 2 background.
  - 3 holder-name spelling variants.
  - 2 licence numbers that are absent from the registry.

**Rules** (`seeds_manifest.json` → `rules.seeded_rules`)
- **RD-01 Art.6:** the buffer is 300 m in v1 and 500 m in v2.
- **RD-01 Art.4 v2:** the trade licence must stay valid for at least 90 days after the application date.
- **Survey age:** RD-05 Art.2 allows 24 months; RD-01 Art.9 v2 allows only 12 months for coastal sites.
- **Limits:** PM10 at most 12.5 t/yr (RD-02 Art.3), waste stored at most 60 days (RD-03 Art.3), abstraction at most 50,000 m³/yr (RD-04 Art.3).
- **Vague on purpose:** RD-03 Art.5 ("adequate secondary containment").
- **No clause at all** on noise, desalination brine, data-centre cooling water, activities under 30 days, or solar farms.

**Core packs: complete (40)**

| Group | Packs | Expected |
|---|---|---|
| Clean | P1-002 004 007 013 015 017 018 019, P2-003 005 006 007 010 011 017 020 | all met |
| Site 300–500 m from a protected area | P1-003 009 012, P2-001 008 009 | not met under v2; met before the cutover (P1-009, P2-001) |
| Above a limit | P1-005 008 (PM10), P1-020 (waste storage), P2-004 013 019 (abstraction) | not met |
| Not covered by any rule | P1-010 (temporary activity), P1-011 (solar farm), P2-002 (desalination brine), P2-012 (data-centre cooling) | unclear |
| Prior refusal or open enforcement | P1-001 P2-016 (refusal), P1-014 P2-018 (enforcement) | high tier, section head |
| Licence expiring within 90 days | P1-006 016, P2-014 015 | RD-01 Art.4 v2 not met |

Cross-cutting seeds among these packs:
- **Dated before the cutover:** P1-007 009 013, P2-001 006 010.
- **Holder-name variant:** P1-017 018, P2-011.
- **500–700 m from a protected area:** P1-004, P2-020.
- **Coastal survey conflict:** P2-004 (after the cutover, fails) and P2-006 (before the cutover, passes).
- **Unclear containment:** P1-001, P1-014.

**Core packs: seeded (20)**

| Seed | Packs |
|---|---|
| Missing signature | P1-026 029, P2-021 |
| Expired trade licence | P1-022 023, P2-025 |
| Expired licence + missing survey (A2) | P2-023 |
| Missing survey | P2-022 |
| Mismatched coordinates | P1-021 025, P2-026 030 |
| Inconsistent quantities | P1-024 030, P2-027 029 |
| Unreadable scan | P1-027, P2-028 |
| Secondary-language-only document (pending) | P2-024 |
| Hostile instruction on page 2 of the emissions summary (A4, R-01) | P1-028 |

**Extra packs (45)**
- **Fairness (FV-001 to FV-020):** copies of 20 complete packs with only the applicant name and nationality changed.
- **Resubmissions (RS-001 to RS-015):**
  - 10 fixed.
  - 5 still defective: RS-001 and RS-015 (renewed licence still expired), RS-003 and RS-011 (licence number not in the registry), RS-014 (coordinates still mismatched).
- **Red team:** RT-02 (white 2 pt text), RT-03 (second-language text, pending), RT-04 ("survey present"), RT-05 ("cite v1"), RT-10 (asks for other applicants' data).
- **Performance (PF-001 to PF-005):** clean packs padded to 30 pages each.

**Precedents:**
- 20 close matches, 20 distant, 10 decoys and 10 generic.
- 5 cite RD-01 v1: DEC-0032, 0073, 0091, 0141 and 0150.
- A gold top 3 exists for 20 core packs.

**Pending the second language** (`seeds_manifest.json` → `pending_secondary_language`, `eval.reserved_pending_secondary_language`):
- **18 packs:** P1-005 006 009 014 015 026, P2-006 008 010 016 020 024 030, RT-03, FV-004 017 018 019.
- **Evaluation items:** 40 retrieval questions, R-03's text, trajectory V15, and the second-language templates.

## Known limitations
- **Cleaner than reality.** Synthetic packs are more uniform than real submissions: no handwriting and few layouts. Extraction scores will be optimistic.
- **Small corpus.** The rule corpus is 60 clauses, so retrieval recall will be higher than on a real corpus of hundreds of documents.
- **Labels by construction.** The labels come from the generator's own rules engine and were spot-checked by hand. No independent domain expert reviewed them (A-19). Precedent relevance is judged by a written scoring rule (A-26).
- **Weak nationality test.** Fairness pairs change name and nationality, but every name comes from the `en_US` Faker list, so the test mainly measures sensitivity to names.
- **Second-RFI rate is fixed by the data.** 5 of the 15 resubmissions are still defective by design (A-31), so the ≤ 15% target can't be shown with fixed data.
- **Drafted red-team cases.** R-06 to R-21 (except R-10) were drafted by the candidate because the security pack isn't available (A-30).
- **English only.** The second language is deferred, so per-language metrics and right-to-left rendering are not covered yet.
