# Fake Daraja Server Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev,test]"

# Copy source
COPY packages/integrations/fake_daraja ./packages/integrations/fake_daraja
COPY packages/core ./packages/core

# Expose port
EXPOSE 8082

# Health check
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8082/health', timeout=2)"

# Run the server
CMD ["python", "-m", "packages.integrations.fake_daraja.main"]