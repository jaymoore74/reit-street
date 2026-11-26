# Contributing

## Setup

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install -c constraints.txt -e ".[dev]"
```

Integration tests need a MySQL 8 server and a user that can drop and create the test
database. The tests destroy that database, so never point them at real data:

```bash
export REITSTREET_TEST_DB_URL="mysql+pymysql://tester:testpw@127.0.0.1:3306/rs_test"
python -m pytest -q -p no:logging
```

Without `REITSTREET_TEST_DB_URL` the integration tests are skipped and only unit tests run.

## Before you push

```bash
ruff format .
ruff check .
mypy
python -m pytest -q -p no:logging
```

CI runs the same on Python 3.11 and 3.13 against MySQL 8.4.

## Rules

- Work on a branch; merge to `main` by pull request with CI green.
- Every schema change is an Alembic revision with comments on every table and column,
  and a regenerated `docs/data-dictionary.md` ([runbook](docs/runbooks/schema-change.md)).
- `dl_*` views are the client contract: additive changes only, or a new versioned view.
- Parsers are pure functions from bytes to records; add a fixture test for every new
  source format and every bug.
- Never update or delete raw data, and never update warehouse values in place.
- Never commit `config.ini`, data, credentials or downloaded documents.
- Dependencies: change `pyproject.toml` ranges and pin exact versions in
  `constraints.txt` in the same PR.
- Significant design choices get an ADR in `docs/adr/`.
- Note user-visible changes in `CHANGELOG.md`.
