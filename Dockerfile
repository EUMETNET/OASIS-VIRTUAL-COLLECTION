# ── Stage 1: dependency installation ──────────────────────────────────────────
FROM registry.met.no/baseimg/ubuntu:26.04 AS builder

# Copy uv from the official image — single static binary, no extra deps needed
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python

# Install Python into a fixed, non-home path so it is easy to copy and
# unambiguous in the runtime stage
RUN uv python install 3.13

# Prevent any further automatic Python downloads in subsequent steps
ENV UV_PYTHON_DOWNLOADS=never

# Install production dependencies.
# pyproject.toml and uv.lock are bind-mounted for the duration of each RUN
# command only — they are never written into an image layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev


# ── Stage 2: minimal runtime image ────────────────────────────────────────────
FROM registry.met.no/baseimg/ubuntu:26.04 AS runtime

# Create the non-root user before any COPY --chown references it
RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --no-create-home --shell /sbin/nologin appuser

WORKDIR /app

# Copy the Python interpreter installation, owned by the non-root user.
# The venv's python symlink resolves into this directory at runtime, so it
# must be present and executable without root privileges.
COPY --from=builder --chown=appuser:appgroup /opt/python /opt/python

# Copy the virtual environment and application code
COPY --from=builder --chown=appuser:appgroup /app/.venv  /app/.venv
COPY --from=builder --chown=appuser:appgroup /app/app    ./app

# Copy default config (can be overridden at runtime via a bind mount)
COPY --chown=appuser:appgroup config ./config

# Put the venv on PATH so uvicorn resolves without an absolute path
ENV PATH="/app/.venv/bin:$PATH"

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
