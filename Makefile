.DEFAULT_GOAL := help

PYTHON := .venv/bin/python
PIP := .venv/bin/pip
RUFF := .venv/bin/ruff
PYTEST := .venv/bin/python -m pytest

.PHONY: help venv install pre-commit lint lint-fix format compile test test-quiet deps-check check server agent cli session clean

help: ## Show this list of commands
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv if it doesn't exist yet
	@test -d .venv || python3 -m venv .venv

install: venv ## Install the project (editable) plus dev tools (ruff, pytest, pre-commit)
	$(PIP) install -e .
	$(PIP) install ruff pytest pre-commit

pre-commit: ## Install the pre-commit git hook
	.venv/bin/pre-commit install

lint: ## Check lint/formatting rules (ruff check)
	$(RUFF) check .

lint-fix: ## Check and auto-fix what ruff can fix
	$(RUFF) check --fix .

format: ## Reformat the codebase (ruff format)
	$(RUFF) format .

compile: ## Byte-compile every tracked .py file (fast syntax-error check)
	@find . -name "*.py" -not -path "./.venv/*" -not -path "./build/*" \
		| xargs -I{} $(PYTHON) -m py_compile {}

test: ## Run the full test suite, verbose
	$(PYTEST) tests/ -v

test-quiet: ## Run the full test suite, quiet output
	$(PYTEST) tests -q

deps-check: ## Verify the agent/ and workspace/ dependency-direction boundaries
	@echo "=== agent/ must not import tool/llm/workspace ==="
	@! grep -rn "^import workspace\|^from workspace\|^import tool\b\|^from tool\b\|^import llm\b\|^from llm\b" agent/ 2>/dev/null
	@echo "=== workspace/ must not import tool/service ==="
	@! grep -rn "^import tool\b\|^from tool\b\|^import service\b\|^from service\b" workspace/ 2>/dev/null
	@echo "boundaries clean"

check: lint compile deps-check test-quiet ## Run lint + compile + boundary checks + tests (full verification)

server: ## Run the websocket agent server (agent-server)
	.venv/bin/agent-server

agent: ## Run the installed agent CLI console script (pass a path via ARGS, e.g. make agent ARGS=frontend/)
	.venv/bin/agent $(ARGS)

cli: ## Run via python main.py, loading .env first (pass a path via ARGS, e.g. make cli ARGS=frontend/)
	$(PYTHON) main.py $(ARGS)

session: ## Run the agent-session CLI (pass args via ARGS, e.g. make session ARGS="create test_session")
	.venv/bin/agent-session $(ARGS)

clean: ## Remove caches and build artifacts
	rm -rf __pycache__ .pytest_cache .ruff_cache *.egg-info dist build
	find . -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
