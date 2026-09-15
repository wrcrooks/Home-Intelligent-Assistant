# Home Assistant's Supervisor always uses an app's own folder as the Docker
# build context (confirmed against supervisor/apps/build.py: it runs
# `docker buildx build .` with this directory bind-mounted at /addon) -- a
# Dockerfile living in hia/ cannot COPY from ../backend or ../frontend, even
# though they're right there in the same repo. So this clones the repo's own
# source in a build stage rather than assuming co-location. Not the only
# option (see docs/HANDOFF.md for the others considered), but the one that
# doesn't require restructuring the whole repo around the add-on's build
# constraints, and it's genuinely testable locally against real pushed
# commits, unlike the alternatives.
#
# BUILD_REF defaults to main; a real release process (not built yet) would
# pin this to a tag matching config.yaml's `version`.
#
# Both ARGs here are used in FROM lines below, and Docker only makes an ARG
# visible to a FROM if it was declared before the *first* FROM in the file --
# declaring BUILD_FROM further down, right before the FROM that uses it,
# reliably fails with "base name should not be blank".
ARG BUILD_REF=main
# Explicit default rather than relying on Supervisor-injected BUILD_FROM --
# since Supervisor 2026.04.0, that's only provided when a build.yaml exists,
# and this app deliberately doesn't have one (a per-arch build.yaml would add
# a second, redundant place arch handling lives, on top of the source clone
# above).
#
# base-debian, not the Alpine-based base: verified directly (not assumed) --
# `duckdb` has no musllinux wheel on PyPI and needs a full C++ toolchain
# (CMake + g++) to build from source, which a plain Alpine Python image
# doesn't have. Confirmed by actually running `uv sync` for this exact
# lockfile inside python:3.13-alpine (failed on duckdb) and python:3.13-slim
# (glibc/Debian; succeeded, all 41 packages, all prebuilt wheels). See
# docs/HANDOFF.md.
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:bookworm

FROM node:24-slim AS source
ARG BUILD_REF
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${BUILD_REF}" \
    https://github.com/wrcrooks/Home-Intelligent-Assistant.git /src

FROM source AS frontend-build
WORKDIR /src/frontend
RUN npm ci
RUN npm run build

FROM ${BUILD_FROM}

# curl explicitly installed rather than assumed present on BUILD_FROM: a
# `curl ... | sh` pipe with curl missing doesn't fail the RUN step at all --
# curl exits 127, but sh receives its (empty) stdout, itself exits 0 doing
# nothing, and a plain `sh -c "a | b"` only checks b's exit code -- so a
# missing curl means uv silently never gets installed, discovered only later
# (and confusingly) at `uv: not found`. Downloading to a file first, then
# running it as a separate step, means a failed download actually fails the
# build instead of silently no-op'ing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh -o /tmp/install-uv.sh
RUN sh /tmp/install-uv.sh && rm /tmp/install-uv.sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app/backend
# src/ copied in before `uv sync`, not after: `uv sync` creates an editable
# install of hia itself, and hatchling's editable build needs src/hia to
# actually exist at that point to capture it -- copying src/ afterward (the
# first version of this Dockerfile did, for slightly better layer caching)
# produced a venv where `import hia` failed outright at runtime, caught only
# by actually running the built image, not by the build succeeding. README.md
# is needed too: backend/pyproject.toml declares `readme = "README.md"`, and
# hatchling refuses to build the package's own metadata at all without it
# physically present.
COPY --from=source \
    /src/backend/pyproject.toml /src/backend/uv.lock /src/backend/.python-version \
    /src/backend/README.md ./
COPY --from=source /src/backend/src ./src
RUN uv python install "$(cat .python-version)" \
    && uv sync --locked --no-dev

COPY --from=frontend-build /src/frontend/dist /app/frontend/dist

COPY rootfs /
# Explicit, not relying on the source checkout's own file mode bits to survive
# git clone + COPY correctly: s6 refused to spawn these at all on a real
# install ("unable to spawn ./run: Permission denied") because they were
# authored on a Windows dev machine, which doesn't track a Unix executable
# bit, and git's index for them ended up as plain 100644 files. Fixed in the
# repository's own git file mode too, but this is the belt-and-suspenders
# version that doesn't depend on getting that right forever.
RUN chmod +x /etc/services.d/hia/run /etc/services.d/hia/finish

LABEL \
    org.opencontainers.image.title="Home Intelligent Assistant" \
    org.opencontainers.image.description="AI/ML companion for Home Assistant" \
    org.opencontainers.image.source="https://github.com/wrcrooks/Home-Intelligent-Assistant" \
    io.hass.type="addon" \
    io.hass.arch="amd64|aarch64"
