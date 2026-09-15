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
uv run hia serve           # the real thing: ingest + REST API + live WS relay, one process
uv run hia backfill \
  --db-path /path/to/home-assistant_v2.db   # read-only; SQLite only for now
uv run hia data-quality    # report on what's in the event store
uv run hia ingest          # standalone ingest-only tool — see the warning below
```

`watch`/`serve` are P0/P1/P2's reconnect exit criterion (docs/04-roadmap.md): they
should keep streaming across a Home Assistant Core restart, reconnecting and
resubscribing on their own. `serve` owns the event store outright — connects to HA,
ingests every subscribed event, serves `GET /api/entities` / `GET /api/data-quality`
and `WS /api/ws/events` off `{HIA_DATA_DIR}/hia.duckdb` (default
`./.data/hia.duckdb`, gitignored), all through one connection, on `:8099` by default.

**`hia ingest`, `hia backfill`, and `hia data-quality` must never run at the same
time as `hia serve`** (or each other, except `data-quality` can coexist with another
`data-quality`) against the same `HIA_DATA_DIR` — DuckDB allows a database file to
be opened in *either* read-write (exactly one process) *or* read-only (any number of
processes, none writing), never a mix of one writer and separate readers. Verified
against a real Linux container; see `hia.api.state`'s module docstring. `--db-path`
defaults to `HIA_RECORDER_DB_PATH` if set.

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
