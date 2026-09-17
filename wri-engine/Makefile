VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install generate test lint fmt api ui demo clean

install:
	python3 -m venv $(VENV) || true
	$(PIP) install -q -e ".[ui,dev]"

generate:
	$(PY) -m wri_engine.generator.cli --seed 20260917 --out data/synthetic

test:
	$(VENV)/bin/pytest -q --cov --cov-report=term-missing --cov-fail-under=85

lint:
	$(VENV)/bin/ruff check src tests app
	$(VENV)/bin/ruff format --check src tests app

fmt:
	$(VENV)/bin/ruff format src tests app
	$(VENV)/bin/ruff check --fix src tests app

api:
	$(VENV)/bin/uvicorn wri_engine.api.main:app --reload --port 8000

ui:
	$(VENV)/bin/streamlit run app/streamlit_app.py --server.port 8501

demo: generate
	@echo "Now run 'make api' in one terminal and 'make ui' in another."

clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
