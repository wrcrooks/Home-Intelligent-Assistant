# hia backend

The Python backend for Home Intelligent Assistant. See the repo root
[README](../README.md) and [docs/](../docs/) for the project as a whole — this file
is just "how do I run this on my machine."

## Setup

Requires [uv](https://docs.astral.sh/uv/). It will fetch the pinned Python version
(3.13, see `.python-version`) on first run — no separate Python install needed.

```sh
cd backend
uv sync                 # installs runtime + dev dependencies into .venv
cp .env.example .env    # fill in HIA_HA_URL / HIA_HA_TOKEN
```

`HIA_HA_TOKEN` is a long-lived access token from your HA user profile
(Settings → your profile → Security → Long-Lived Access Tokens), or point at the
throwaway instance in `../compose/dev-ha/` if you don't want to use a real house yet.

## Running it

```sh
uv run hia check           # connect, authenticate, disconnect — confirms config is right
uv run hia watch           # stream live state_changed events to stdout until Ctrl-C
uv run hia registry        # dump the entity/device/area/floor/label registries as JSON
uv run hia ingest          # like watch, but persisted into the DuckDB event store
uv run hia backfill \
  --db-path /path/to/home-assistant_v2.db   # read-only; SQLite only for now
uv run hia data-quality    # report on what's in the event store
```

`watch`/`ingest` are P0/P1's reconnect exit criterion (docs/04-roadmap.md): they
should keep streaming across a Home Assistant Core restart, reconnecting and
resubscribing on their own. `ingest` writes to `{HIA_DATA_DIR}/hia.duckdb`
(default `./.data/hia.duckdb`, gitignored); `backfill` writes into the same file.
`--db-path` defaults to `HIA_RECORDER_DB_PATH` if set.

## Checks

```sh
uv run ruff check .
uv run mypy src
uv run pytest
```

All three run in CI (`.github/workflows/ci.yml`) on every push and PR. The test
suite (`tests/`) runs against a fake in-process HA websocket server
(`tests/conftest.py`) — no real Home Assistant instance or network access required.

## GPU

This backend's runtime and CI targets are CPU-only by design (docs/CLAUDE.md,
docs/06-model-training.md) — the *shipped add-on* has to run on whatever a typical
Home Assistant host is, and most of those have no GPU. That constraint is about the
add-on's runtime requirements, not this repository's tooling: nothing here requires
CUDA, and nothing here should ever come to require it. Where a training machine
happens to have a CUDA-capable GPU (this project's primary dev box has an RTX 3060),
model code added from P4/P5 onward should prefer it opportunistically — the standard
`torch.device("cuda" if torch.cuda.is_available() else "cpu")` pattern — purely to
make local iteration faster, while still running correctly with no GPU present.
