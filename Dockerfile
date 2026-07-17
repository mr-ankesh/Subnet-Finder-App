# ── Build stage ──────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
# Generous timeout + retries: the Azure SDK / pandas wheels are large and
# slow mirrors otherwise fail the build with read timeouts.
RUN pip install --no-cache-dir --timeout 120 --retries 5 --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Application code (all top-level modules: app, config, models, db_utils,
# audit, search, settings_store, naming, notifications, azure_tools, agents)
COPY *.py ./
COPY templates/ templates/
COPY static/    static/

# Non-root runtime user; data dir is replaced by the PVC mount in K8s but
# must exist and be writable for local `docker run` too.
RUN groupadd -r -g 10001 app && useradd -r -u 10001 -g app app \
    && mkdir -p data && chown -R app:app /app
USER 10001

EXPOSE 8080

# Container-level health check (K8s uses its own probes on /health)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"

# Run with gunicorn — single worker (SQLite), threads for concurrency,
# 120s timeout for long Azure operations, logs to stdout/stderr.
CMD ["gunicorn", \
     "--workers=1", \
     "--threads=8", \
     "--bind=0.0.0.0:8080", \
     "--timeout=120", \
     "--graceful-timeout=30", \
     "--access-logfile=-", \
     "--error-logfile=-", \
     "app:app"]
