PY      ?= .venv/bin/python
SEED    ?= $(shell $(PY) -c "import yaml;print(yaml.safe_load(open('config.yaml'))['seed'])" 2>/dev/null)
GEN     := data/generators
CHK     := data/checks
ARGS    := $(if $(SEED),--seed $(SEED),)

# WeasyPrint (pango) and pyodbc (unixodbc) load Homebrew libraries on macOS
export DYLD_FALLBACK_LIBRARY_PATH := $(shell brew --prefix 2>/dev/null)/lib:$(DYLD_FALLBACK_LIBRARY_PATH)

.DEFAULT_GOAL := help
.PHONY: help venv registry rules precedents packs render eval templates check db-up db-load db-down test all clean

help:  ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv:  ## Create .venv and install requirements (uv)
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) -r requirements.txt

registry:  ## Stage 2: registry CSVs + pre-assigned seeded packs
	$(PY) $(GEN)/gen_registry.py $(ARGS)

rules:  ## Stage 4: rule corpus (DOCX, PDF, chunks.jsonl, config_seed.sql)
	$(PY) $(GEN)/gen_rules.py $(ARGS)

precedents:  ## Stage 5: precedent summaries + gold top-3
	$(PY) $(GEN)/gen_precedents.py $(ARGS)

packs:  ## Stage 6: pack facts + labels
	$(PY) $(GEN)/gen_packs.py $(ARGS)

render:  ## Stage 7: render pack PDFs and scans
	$(PY) $(GEN)/render_packs.py $(ARGS)

eval:  ## Stage 8: evaluation sets (retrieval, unanswerable, trajectories, red team, consistency, fairness)
	$(PY) $(GEN)/gen_eval.py $(ARGS)

templates:  ## Stage 8: decision-pack and RFI-letter template examples (PDF, DOCX)
	$(PY) $(GEN)/render_templates.py $(ARGS)

check:  ## Run every data check that exists
	@for c in $(CHK)/check_*.py; do echo "== $$c"; $(PY) $$c || exit 1; done

db-up:  ## Start local SQL Server 2022 (docker)
	docker compose up -d

# msodbcsql18 loads OpenSSL from brew's default "openssl" (now 4.x, unsupported);
# point it at openssl@3 for this target only.
db-load:  ## Apply schema.sql and load registry CSVs
	DYLD_LIBRARY_PATH=$(shell brew --prefix openssl@3 2>/dev/null)/lib $(PY) data/loaders/load_sql_local.py

db-down:  ## Stop local SQL Server
	docker compose down

test:  ## Run pytest
	$(PY) -m pytest -q

all: registry rules precedents packs render eval templates check  ## Rebuild everything, then check

clean:  ## Delete generated output
	rm -rf data/output/*
