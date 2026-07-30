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

# Network diagnostics tools — so you can ping/curl/telnet/tracepath from the
# pod itself (kubectl exec) as well as from the connector VMs.
#   iputils-ping → ping ;  iputils-tracepath → tracepath ;
#   inetutils-telnet → telnet ;  iproute2 → ss ;  curl ;
#   dnsutils → dig/nslookup ;  netcat-openbsd → nc ;  traceroute
RUN apt-get update && apt-get install -y --no-install-recommends \
        iputils-ping iputils-tracepath inetutils-telnet iproute2 curl dnsutils \
        netcat-openbsd traceroute \
    && rm -rf /var/lib/apt/lists/*

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

# gunicorn's control server writes a socket under $HOME. `useradd -r` points HOME
# at /home/app but never creates it, so under a read-only-rootfs / restricted
# securityContext that write fails ("Permission denied: '/home/app'"). Point HOME
# at a writable path so it works regardless of how the pod mounts the filesystem.
ENV HOME=/tmp

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
