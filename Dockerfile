# ─────────────────────────────────────────────────────────────
# NetTopoView / CogniShield — Multi-stage Docker build
# Stage 1: Build React frontend
# Stage 2: Run FastAPI backend + serve static frontend
# ─────────────────────────────────────────────────────────────

# ── Stage 1: Frontend build ──────────────────────────────────
FROM node:20-alpine AS frontend-build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install

COPY frontend/ ./
RUN npm run build


# ── Stage 2: Backend runtime ─────────────────────────────────
FROM python:3.11-slim AS runtime

# System deps for Ansible, SSH, crypto, lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    sshpass \
    git \
    curl \
    gcc \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Backend source
COPY backend/ ./backend/
COPY topology_store/ ./topology_store/

# Built frontend static files → serve from FastAPI
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Ensure DB and knowledge_base directories exist
RUN mkdir -p backend/db backend/chroma_db backend/knowledge_base backend/ansible/playbooks

# Copy docs for reference
COPY docs/ ./docs/

# Environment defaults (override via docker-compose or .env)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend \
    CRAWL_SEEDS=192.168.1.1 \
    CRAWL_USER="" \
    CRAWL_PASSWORD="" \
    CRAWL_DRIVER=cisco_ios \
    CRAWL_WORKERS=6 \
    INFLUXDB_URL=http://influxdb:8086 \
    INFLUXDB_TOKEN="" \
    INFLUXDB_ORG=netauto \
    INFLUXDB_BUCKET=telegraf \
    GEMINI_API_KEY="" \
    NETBOX_URL="" \
    NETBOX_TOKEN="" \
    JWT_SECRET_KEY=change-me-in-production \
    DEFAULT_ADMIN_PASSWORD=admin

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/monitor/status || exit 1

WORKDIR /app/backend

CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
