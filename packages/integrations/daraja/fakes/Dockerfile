FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Copy application
COPY main.py .

# Expose port
EXPOSE 8081

# Run
CMD ["python", "main.py"]