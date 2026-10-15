FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-cache-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY packages/core ./packages/core
COPY packages/integrations ./packages/integrations
COPY packages/agent ./packages/agent
COPY apps/api ./apps/api

# Install package in development mode
RUN uv pip install -e ./packages/core
RUN uv pip install -e ./packages/integrations/meta
RUN uv pip install -e ./packages/integrations/daraja
RUN uv pip install -e ./packages/integrations/aggregators
RUN uv pip install -e ./packages/agent
RUN uv pip install -e ./apps/api

# Expose port
EXPOSE 8000

# Run
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]