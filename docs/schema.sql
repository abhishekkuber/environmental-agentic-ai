/* =====================================================================
   UC1 - Permit and NOC Processing Agent
   Azure SQL schema (T-SQL). Exercise content: fictional data only.

   Schemas
     registry : synthetic system-of-record data (applicants, sites, ...)
     config   : checklists, risk factors, thresholds (policy-owned)
     work     : agent working store (runs, extractions, findings, drafts)
     audit    : append-only ledger of every agent step and human action

   Access model
     agent_reader  -> SELECT on registry views + config only
     agent_writer  -> INSERT/UPDATE on work, INSERT on audit
     intake_api    -> INSERT on work.submissions, INSERT on audit
     Nobody gets UPDATE/DELETE on audit (ledger table blocks it anyway).
   ===================================================================== */

CREATE SCHEMA registry;
GO
CREATE SCHEMA config;
GO
CREATE SCHEMA work;
GO
CREATE SCHEMA audit;
GO

/* ---------------------------------------------------------------------
   REGISTRY
   --------------------------------------------------------------------- */

CREATE TABLE registry.applicants (
    applicant_id        VARCHAR(12)     NOT NULL PRIMARY KEY,          -- APP-0001
    applicant_type      VARCHAR(20)     NOT NULL
        CHECK (applicant_type IN ('company', 'consultant')),
    name_en             NVARCHAR(200)   NOT NULL,
    name_ar             NVARCHAR(200)   NULL,
    nationality         VARCHAR(40)     NULL,       -- fairness tests only; never exposed to agents
    national_id         VARCHAR(20)     MASKED WITH (FUNCTION = 'partial(0,"XXXX",4)') NULL,
    company_reg_no      VARCHAR(20)     NULL,
    email               NVARCHAR(256)   MASKED WITH (FUNCTION = 'email()') NULL,
    phone               VARCHAR(30)     MASKED WITH (FUNCTION = 'default()') NULL,
    preferred_language  CHAR(2)         NOT NULL DEFAULT 'en'
        CHECK (preferred_language IN ('en', 'ar')),
    created_at          DATE            NOT NULL
);

CREATE TABLE registry.sites (
    site_id             VARCHAR(12)     NOT NULL PRIMARY KEY,          -- SITE-0001
    applicant_id        VARCHAR(12)     NOT NULL REFERENCES registry.applicants(applicant_id),
    plot_id             VARCHAR(12)     NOT NULL UNIQUE,
    address_en          NVARCHAR(300)   NULL,
    address_ar          NVARCHAR(300)   NULL,
    district            NVARCHAR(60)    NULL,
    land_use            VARCHAR(30)     NOT NULL
        CHECK (land_use IN ('industrial','commercial','agricultural','residential_edge','coastal')),
    latitude            DECIMAL(9,6)    NOT NULL,
    longitude           DECIMAL(9,6)    NOT NULL
);

CREATE TABLE registry.protected_areas (
    protected_area_id   VARCHAR(10)     NOT NULL PRIMARY KEY,          -- PA-01
    name_en             NVARCHAR(150)   NOT NULL,
    name_ar             NVARCHAR(150)   NULL,
    area_type           VARCHAR(30)     NOT NULL
        CHECK (area_type IN ('mangrove','wetland','marine_reserve','wildlife_reserve','aquifer_protection')),
    centre_latitude     DECIMAL(9,6)    NOT NULL,
    centre_longitude    DECIMAL(9,6)    NOT NULL,
    radius_m            INT             NOT NULL
);

CREATE TABLE registry.licences (
    licence_id          VARCHAR(12)     NOT NULL PRIMARY KEY,
    applicant_id        VARCHAR(12)     NOT NULL REFERENCES registry.applicants(applicant_id),
    site_id             VARCHAR(12)     NULL REFERENCES registry.sites(site_id),
    licence_type        VARCHAR(30)     NOT NULL
        CHECK (licence_type IN ('trade','environmental_permit','well_permit')),
    licence_number      VARCHAR(20)     NOT NULL UNIQUE,
    holder_name         NVARCHAR(200)   NOT NULL,
    issue_date          DATE            NOT NULL,
    expiry_date         DATE            NOT NULL,
    status              VARCHAR(20)     NOT NULL
        CHECK (status IN ('active','expired','suspended')),
    CONSTRAINT ck_licence_dates CHECK (expiry_date > issue_date)
);

CREATE TABLE registry.prior_decisions (
    decision_id         VARCHAR(12)     NOT NULL PRIMARY KEY,          -- DEC-0001
    applicant_id        VARCHAR(12)     NOT NULL REFERENCES registry.applicants(applicant_id),
    site_id             VARCHAR(12)     NOT NULL REFERENCES registry.sites(site_id),
    permit_type         CHAR(4)         NOT NULL CHECK (permit_type IN ('PT-1','PT-2')),
    decision_date       DATE            NOT NULL,
    outcome             VARCHAR(30)     NOT NULL
        CHECK (outcome IN ('approved','approved_with_conditions','refused')),
    reason_summary_en   NVARCHAR(2000)  NULL,
    reason_summary_ar   NVARCHAR(2000)  NULL,
    clauses_cited       NVARCHAR(500)   NULL,      -- JSON array of clause IDs
    has_precedent_summary BIT           NOT NULL DEFAULT 0
);

CREATE TABLE registry.open_cases (
    open_case_id        VARCHAR(12)     NOT NULL PRIMARY KEY,          -- OC-0001
    applicant_id        VARCHAR(12)     NOT NULL REFERENCES registry.applicants(applicant_id),
    site_id             VARCHAR(12)     NULL REFERENCES registry.sites(site_id),
    case_type           VARCHAR(30)     NOT NULL
        CHECK (case_type IN ('enforcement','complaint','pending_application','compliance_audit')),
    status              VARCHAR(20)     NOT NULL DEFAULT 'open',
    opened_date         DATE            NOT NULL,
    summary             NVARCHAR(1000)  NULL
);
GO

/* Read-only views for agents: only the fields a case needs.
   No nationality, no national_id. */

CREATE VIEW registry.v_applicant_history AS
SELECT  a.applicant_id,
        a.applicant_type,
        a.name_en,
        a.name_ar,
        a.preferred_language,
        (SELECT COUNT(*) FROM registry.prior_decisions d
          WHERE d.applicant_id = a.applicant_id AND d.outcome = 'refused')      AS prior_refusals,
        (SELECT COUNT(*) FROM registry.prior_decisions d
          WHERE d.applicant_id = a.applicant_id)                                AS prior_decisions,
        (SELECT COUNT(*) FROM registry.open_cases o
          WHERE o.applicant_id = a.applicant_id AND o.status = 'open')          AS open_cases,
        (SELECT COUNT(*) FROM registry.open_cases o
          WHERE o.applicant_id = a.applicant_id AND o.status = 'open'
            AND o.case_type = 'enforcement')                                    AS open_enforcement_cases
FROM registry.applicants a;
GO

CREATE VIEW registry.v_site_licences AS
SELECT  s.site_id, s.applicant_id, s.plot_id, s.district, s.land_use,
        s.latitude, s.longitude,
        l.licence_id, l.licence_type, l.licence_number, l.holder_name,
        l.issue_date, l.expiry_date, l.status AS licence_status
FROM registry.sites s
LEFT JOIN registry.licences l ON l.applicant_id = s.applicant_id;
GO

CREATE VIEW registry.v_prior_decisions AS
SELECT  decision_id, applicant_id, site_id, permit_type, decision_date,
        outcome, reason_summary_en, reason_summary_ar, clauses_cited
FROM registry.prior_decisions;
GO

CREATE VIEW registry.v_open_cases AS
SELECT  open_case_id, applicant_id, site_id, case_type, status, opened_date, summary
FROM registry.open_cases
WHERE status = 'open';
GO

/* ---------------------------------------------------------------------
   CONFIG (policy-owned; read by agents)
   --------------------------------------------------------------------- */

CREATE TABLE config.checklist_items (
    permit_type         CHAR(4)         NOT NULL CHECK (permit_type IN ('PT-1','PT-2')),
    item_code           VARCHAR(10)     NOT NULL,                      -- C1, C2, P1-1, P2-2 ...
    item_name_en        NVARCHAR(150)   NOT NULL,
    item_name_ar        NVARCHAR(150)   NULL,
    required            BIT             NOT NULL DEFAULT 1,
    source_clause_id    VARCHAR(30)     NULL,                          -- e.g. RD-07 Art.2
    validation_rules    NVARCHAR(MAX)   NULL,                          -- JSON
    PRIMARY KEY (permit_type, item_code)
);

CREATE TABLE config.risk_factors (
    factor_code         VARCHAR(40)     NOT NULL PRIMARY KEY,          -- prior_refusal, in_buffer ...
    description_en      NVARCHAR(300)   NOT NULL,
    weight              DECIMAL(5,2)    NOT NULL,
    active              BIT             NOT NULL DEFAULT 1
);

CREATE TABLE config.risk_tiers (
    tier                VARCHAR(10)     NOT NULL PRIMARY KEY CHECK (tier IN ('low','medium','high')),
    min_score           DECIMAL(6,2)    NOT NULL,
    approver_role       VARCHAR(30)     NOT NULL                       -- technical_reviewer / section_head
);

CREATE TABLE config.thresholds (
    threshold_code      VARCHAR(40)     NOT NULL PRIMARY KEY,          -- low_conf_field, coord_tolerance_m ...
    value_numeric       DECIMAL(18,4)   NOT NULL,
    unit                VARCHAR(20)     NULL,
    description_en      NVARCHAR(300)   NULL
);

CREATE TABLE config.prompt_versions (
    prompt_id           VARCHAR(40)     NOT NULL,
    version             VARCHAR(20)     NOT NULL,
    model_deployment    VARCHAR(60)     NOT NULL,
    content_hash        CHAR(64)        NOT NULL,                      -- SHA-256 of prompt file in repo
    released_at         DATETIME2       NOT NULL,
    eval_run_id         VARCHAR(60)     NULL,                          -- evaluation gate evidence
    PRIMARY KEY (prompt_id, version)
);
GO

/* ---------------------------------------------------------------------
   WORK (agent working store)
   --------------------------------------------------------------------- */

CREATE TABLE work.submissions (
    submission_id       VARCHAR(40)     NOT NULL PRIMARY KEY,          -- idempotency key from portal
    case_id             VARCHAR(20)     NOT NULL UNIQUE,               -- matches Dataverse permit_case
    channel             VARCHAR(20)     NOT NULL CHECK (channel IN ('portal_api','sharepoint_drop')),
    permit_type         CHAR(4)         NOT NULL CHECK (permit_type IN ('PT-1','PT-2')),
    department          VARCHAR(30)     NOT NULL CHECK (department IN ('air_industrial','groundwater')),
    applicant_id        VARCHAR(12)     NULL,
    site_id             VARCHAR(12)     NULL,
    application_date    DATE            NOT NULL,                      -- drives in-force clause filter
    language            CHAR(5)         NOT NULL,                      -- en / ar / mixed
    sharepoint_path     NVARCHAR(400)   NOT NULL,
    received_at         DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    parent_case_id      VARCHAR(20)     NULL                           -- set for RFI resubmissions
);

CREATE TABLE work.case_runs (
    run_id              UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWID(),
    case_id             VARCHAR(20)     NOT NULL REFERENCES work.submissions(case_id),
    status              VARCHAR(30)     NOT NULL
        CHECK (status IN ('running','awaiting_review','rfi_drafted','completed','failed_to_queue')),
    started_at          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    ended_at            DATETIME2       NULL,
    steps_used          INT             NOT NULL DEFAULT 0,
    tokens_in           INT             NOT NULL DEFAULT 0,
    tokens_out          INT             NOT NULL DEFAULT 0,
    cost_estimate_usd   DECIMAL(10,4)   NOT NULL DEFAULT 0,
    prompt_bundle_ver   VARCHAR(20)     NOT NULL
);

CREATE TABLE work.documents (
    document_id         UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWID(),
    run_id              UNIQUEIDENTIFIER NOT NULL REFERENCES work.case_runs(run_id),
    file_name           NVARCHAR(260)   NOT NULL,
    declared_type       VARCHAR(10)     NULL,
    classified_type     VARCHAR(10)     NULL,
    class_confidence    DECIMAL(5,4)    NULL,
    page_count          INT             NULL,
    language            CHAR(5)         NULL,
    ocr_min_confidence  DECIMAL(5,4)    NULL,
    injection_flag      BIT             NOT NULL DEFAULT 0
);

CREATE TABLE work.extracted_fields (
    field_id            BIGINT IDENTITY NOT NULL PRIMARY KEY,
    document_id         UNIQUEIDENTIFIER NOT NULL REFERENCES work.documents(document_id),
    field_name          VARCHAR(60)     NOT NULL,
    raw_value           NVARCHAR(1000)  NULL,
    normalised_value    NVARCHAR(1000)  NULL,
    confidence          DECIMAL(5,4)    NULL,
    page                INT             NULL,
    bounding_region     NVARCHAR(400)   NULL,                          -- JSON polygon
    low_confidence      BIT             NOT NULL DEFAULT 0,            -- flagged, not guessed (FR-1.3)
    validation_status   VARCHAR(20)     NULL CHECK (validation_status IN ('valid','invalid','unchecked')),
    validation_reason   NVARCHAR(400)   NULL
);

CREATE TABLE work.completeness_items (
    run_id              UNIQUEIDENTIFIER NOT NULL REFERENCES work.case_runs(run_id),
    item_code           VARCHAR(10)     NOT NULL,
    status              VARCHAR(10)     NOT NULL CHECK (status IN ('present','missing','invalid')),
    reason              NVARCHAR(400)   NULL,
    document_id         UNIQUEIDENTIFIER NULL REFERENCES work.documents(document_id),
    PRIMARY KEY (run_id, item_code)
);

CREATE TABLE work.findings (
    finding_id          UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWID(),
    run_id              UNIQUEIDENTIFIER NOT NULL REFERENCES work.case_runs(run_id),
    requirement_code    VARCHAR(60)     NOT NULL,
    verdict             VARCHAR(12)     NOT NULL CHECK (verdict IN ('met','not_met','unclear')),
    rationale           NVARCHAR(2000)  NOT NULL,
    evidence_field_id   BIGINT          NULL REFERENCES work.extracted_fields(field_id),
    evidence_note       NVARCHAR(500)   NULL,
    officer_note        NVARCHAR(500)   NULL                           -- for 'unclear' (A5)
);

CREATE TABLE work.citations (
    citation_id         BIGINT IDENTITY NOT NULL PRIMARY KEY,
    finding_id          UNIQUEIDENTIFIER NOT NULL REFERENCES work.findings(finding_id),
    chunk_id            VARCHAR(80)     NOT NULL,                      -- AI Search key
    doc_id              VARCHAR(10)     NOT NULL,
    clause_id           VARCHAR(30)     NOT NULL,
    clause_version      VARCHAR(10)     NOT NULL,
    quote               NVARCHAR(1000)  NOT NULL,
    quote_verified      BIT             NOT NULL,                      -- exact substring in chunk (A-25)
    in_force_on_app_date BIT            NOT NULL
);

CREATE TABLE work.risk_scores (
    run_id              UNIQUEIDENTIFIER NOT NULL REFERENCES work.case_runs(run_id),
    factor_code         VARCHAR(40)     NOT NULL REFERENCES config.risk_factors(factor_code),
    factor_value        DECIMAL(10,4)   NOT NULL,
    weighted_score      DECIMAL(10,4)   NOT NULL,
    PRIMARY KEY (run_id, factor_code)
);

CREATE TABLE work.case_outcomes (
    run_id              UNIQUEIDENTIFIER NOT NULL PRIMARY KEY REFERENCES work.case_runs(run_id),
    completeness_verdict VARCHAR(12)    NOT NULL CHECK (completeness_verdict IN ('complete','incomplete')),
    risk_tier           VARCHAR(10)     NULL REFERENCES config.risk_tiers(tier),
    total_risk_score    DECIMAL(10,4)   NULL,
    recommendation      VARCHAR(30)     NULL
        CHECK (recommendation IN ('approve','approve_with_conditions','refuse','request_information')),
    route_to_role       VARCHAR(30)     NULL
);

CREATE TABLE work.drafts (
    draft_id            UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWID(),
    run_id              UNIQUEIDENTIFIER NOT NULL REFERENCES work.case_runs(run_id),
    draft_type          VARCHAR(20)     NOT NULL CHECK (draft_type IN ('decision_pack','rfi_letter','decision_letter')),
    language            CHAR(2)         NOT NULL CHECK (language IN ('en','ar')),
    sharepoint_path     NVARCHAR(400)   NOT NULL,
    template_version    VARCHAR(20)     NOT NULL,
    created_at          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE work.work_queue (
    queue_item_id       BIGINT IDENTITY NOT NULL PRIMARY KEY,
    case_id             VARCHAR(20)     NOT NULL,
    run_id              UNIQUEIDENTIFIER NULL,
    reason              VARCHAR(40)     NOT NULL
        CHECK (reason IN ('dependency_failure','step_budget_exceeded','validation_failure','injection_suspected','other')),
    context             NVARCHAR(MAX)   NOT NULL,                      -- JSON: last state, errors, inputs
    created_at          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    assigned_to         NVARCHAR(256)   NULL,
    resolved_at         DATETIME2       NULL
);

CREATE TABLE work.injection_events (
    event_id            BIGINT IDENTITY NOT NULL PRIMARY KEY,
    run_id              UNIQUEIDENTIFIER NOT NULL REFERENCES work.case_runs(run_id),
    document_id         UNIQUEIDENTIFIER NULL REFERENCES work.documents(document_id),
    source              VARCHAR(20)     NOT NULL CHECK (source IN ('document','officer_prompt')),
    detector            VARCHAR(30)     NOT NULL,                      -- prompt_shields / heuristic
    excerpt_masked      NVARCHAR(500)   NULL,
    detected_at         DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE work.officer_feedback (
    feedback_id         BIGINT IDENTITY NOT NULL PRIMARY KEY,
    run_id              UNIQUEIDENTIFIER NOT NULL REFERENCES work.case_runs(run_id),
    finding_id          UNIQUEIDENTIFIER NULL REFERENCES work.findings(finding_id),
    officer_upn         NVARCHAR(256)   NOT NULL,
    action              VARCHAR(20)     NOT NULL CHECK (action IN ('accept','edit','reject')),
    original_verdict    VARCHAR(12)     NULL,
    new_verdict         VARCHAR(12)     NULL,
    reason              NVARCHAR(1000)  NULL,
    text_change_ratio   DECIMAL(5,4)    NULL,                          -- for "no major edit" (A-21)
    created_at          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

/* ---------------------------------------------------------------------
   AUDIT (append-only ledger; replay source for FR-1.19)
   --------------------------------------------------------------------- */

CREATE TABLE audit.agent_steps (
    step_seq            BIGINT IDENTITY NOT NULL,
    case_id             VARCHAR(20)     NOT NULL,
    run_id              UNIQUEIDENTIFIER NULL,
    step_name           VARCHAR(60)     NOT NULL,      -- intake.extract, compliance.assess, ...
    actor_type          VARCHAR(10)     NOT NULL CHECK (actor_type IN ('agent','tool','human','system')),
    actor_id            NVARCHAR(256)   NOT NULL,      -- agent name, tool name, or officer UPN
    prompt_id           VARCHAR(40)     NULL,
    prompt_version      VARCHAR(20)     NULL,
    model_deployment    VARCHAR(60)     NULL,
    input_hash          CHAR(64)        NULL,
    retrieved_chunk_ids NVARCHAR(MAX)   NULL,          -- JSON array
    tool_calls          NVARCHAR(MAX)   NULL,          -- JSON: name, args (masked), result hash
    output_ref          NVARCHAR(MAX)   NULL,          -- JSON or pointer to work.* rows
    safety_result       NVARCHAR(400)   NULL,          -- Prompt Shields / Content Safety outcome
    human_action        VARCHAR(20)     NULL,          -- approve / edit / reject / return
    human_reason        NVARCHAR(1000)  NULL,
    latency_ms          INT             NULL,
    tokens_in           INT             NULL,
    tokens_out          INT             NULL,
    occurred_at         DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
)
WITH (LEDGER = ON (APPEND_ONLY = ON));
GO

CREATE INDEX ix_audit_case ON audit.agent_steps (case_id, occurred_at);
CREATE INDEX ix_findings_run ON work.findings (run_id);
CREATE INDEX ix_sites_applicant ON registry.sites (applicant_id);
CREATE INDEX ix_licences_applicant ON registry.licences (applicant_id, licence_type);
GO

/* ---------------------------------------------------------------------
   ROLES (bind managed identities after deployment)
   --------------------------------------------------------------------- */

CREATE ROLE agent_reader;
GRANT SELECT ON registry.v_applicant_history TO agent_reader;
GRANT SELECT ON registry.v_site_licences     TO agent_reader;
GRANT SELECT ON registry.v_prior_decisions   TO agent_reader;
GRANT SELECT ON registry.v_open_cases        TO agent_reader;
GRANT SELECT ON registry.protected_areas     TO agent_reader;
GRANT SELECT ON SCHEMA::config               TO agent_reader;

CREATE ROLE agent_writer;
GRANT SELECT, INSERT, UPDATE ON SCHEMA::work TO agent_writer;
GRANT INSERT ON audit.agent_steps            TO agent_writer;
GRANT SELECT ON SCHEMA::config               TO agent_writer;

CREATE ROLE intake_api;
GRANT INSERT, SELECT ON work.submissions     TO intake_api;
GRANT INSERT ON audit.agent_steps            TO intake_api;

CREATE ROLE auditor;
GRANT SELECT ON SCHEMA::audit                TO auditor;
GO

/* After deployment, map managed identities (names are examples):
   CREATE USER [func-permits-tools-dev]  FROM EXTERNAL PROVIDER;  ALTER ROLE agent_reader ADD MEMBER [func-permits-tools-dev];
   CREATE USER [agent-permits-runtime]   FROM EXTERNAL PROVIDER;  ALTER ROLE agent_writer ADD MEMBER [agent-permits-runtime];
   CREATE USER [func-permits-intake-dev] FROM EXTERNAL PROVIDER;  ALTER ROLE intake_api   ADD MEMBER [func-permits-intake-dev];
   Nobody except the data loader (one-off, removed after load) can write to registry.*.
   UNMASK is not granted to any agent identity. */
