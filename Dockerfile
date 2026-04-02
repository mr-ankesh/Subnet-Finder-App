# ── Build stage ──────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app.py .
COPY config.py .
COPY models.py .
COPY notifications.py .
COPY azure_tools.py .
COPY agent_requester.py .
COPY agent_admin.py .
COPY db_utils.py .
COPY templates/ templates/
COPY static/    static/

# Create data directory (will be overridden by volume mount at runtime)
RUN mkdir -p data

# Expose port
EXPOSE 8080

# Run with gunicorn — 2 workers, 120s timeout, logs to stdout/stderr
CMD ["gunicorn", \
     "--workers=1", \
     "--bind=0.0.0.0:8080", \
     "--timeout=120", \
     "--access-logfile=-", \
     "--error-logfile=-", \
     "app:app"]
