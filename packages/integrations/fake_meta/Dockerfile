# Fake Meta Server Dockerfile
FROM python:3.13-slim

WORKDIR /app

# Copy source first
COPY packages/integrations/fake_meta ./packages/integrations/fake_meta
COPY packages/core ./packages/core
COPY packages/integrations/fake_meta/pyproject.toml ./

# Install dependencies
RUN pip install --no-cache-dir -e ".[dev]"

# Expose port
EXPOSE 8081

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import httpx; httpx.get('http://localhost:8081/health', timeout=5)"

# Run the server
CMD ["python", "-m", "packages.integrations.fake_meta.main"]