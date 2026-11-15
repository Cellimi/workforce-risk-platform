VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install lock generate test lint fmt api serve ui demo clean package package-unlicensed

# Every library at the exact version in requirements-dev.lock, each download checked
# against the fingerprint recorded there, so everyone works on what the tests ran on.
install:
	@if command -v uv >/dev/null 2>&1; then \
		uv venv $(VENV) 2>/dev/null || true; \
		uv pip sync --python $(PY) --require-hashes requirements-dev.lock; \
		uv pip install --python $(PY) --no-deps -e .; \
	else \
		python3 -m venv $(VENV) || true; \
		$(PIP) install -q --upgrade pip; \
		$(PIP) install -q --require-hashes -r requirements-dev.lock; \
		$(PIP) install -q --no-deps -e .; \
	fi

# Choose the versions again: the newest that pyproject.toml allows. Run it on purpose
# (a new dependency, a security fix, before a release), then `make install`, `make test`,
# and commit both lock files with the change. requirements.lock is what ships in the image;
# requirements-dev.lock is the same versions plus the tools for testing. Needs uv.
lock:
	uv pip compile pyproject.toml --extra ui --universal --generate-hashes --upgrade \
		--custom-compile-command "make lock" -q -o requirements.lock
	uv pip compile pyproject.toml --extra ui --extra dev --universal --generate-hashes --upgrade \
		--custom-compile-command "make lock" -q -c requirements.lock -o requirements-dev.lock

generate:
	$(PY) -m wri_engine.generator.cli --seed 20260917 --out data/synthetic

test:
	$(VENV)/bin/pytest -q --cov --cov-report=term-missing --cov-fail-under=85

lint:
	$(VENV)/bin/ruff check src tests app deploy
	$(VENV)/bin/ruff format --check src tests app deploy

fmt:
	$(VENV)/bin/ruff format src tests app deploy
	$(VENV)/bin/ruff check --fix src tests app

# The synthetic walkthrough: roles chosen in a header, no sign-in. Never expose this.
api:
	WRI_AUTH_MODE=demo $(VENV)/bin/uvicorn wri_engine.api.main:app --reload --port 8000

# A licensed installation: sign-in required, data kept per engagement.
#   $(VENV)/bin/wri-admin create-user you@firm.example --firm-admin   (first time only)
serve:
	$(VENV)/bin/uvicorn wri_engine.api.main:app --port 8000

ui:
	$(VENV)/bin/streamlit run app/streamlit_app.py --server.port 8501

demo: generate
	@echo "Now run 'make api' in one terminal and 'make ui' in another."

# The install package a licensee receives: the container image with the license
# verification key baked in, the operator's script and the guide. Needs Docker.
#   make package LICENSE_PUBLIC_KEY=~/wri-keys/wri_license_public_key.pem
# Give the VERIFICATION key. The build refuses the signing key.
package:
	@test -n "$(LICENSE_PUBLIC_KEY)" || { \
		echo "usage: make package LICENSE_PUBLIC_KEY=/path/to/wri_license_public_key.pem"; exit 2; }
	$(PY) deploy/package.py --license-key "$(LICENSE_PUBLIC_KEY)"

# The same package with no verification key: it enforces no license keys. For trying the
# install, never for a licensee.
package-unlicensed:
	$(PY) deploy/package.py --unlicensed

clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
