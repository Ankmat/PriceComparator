# ── Stage 1: build dependencies ─────────────────────────────────────────────
# Microsoft's Playwright image ships Chromium + all system deps pre-installed.
# This removes the need to apt-get any browser libraries manually.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy AS base

WORKDIR /app

# Install Python dependencies first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install only the Chromium browser (Firefox/WebKit not needed — saves ~300 MB)
RUN playwright install chromium

# ── Stage 2: copy application code ──────────────────────────────────────────
COPY . .

# ── Runtime config ───────────────────────────────────────────────────────────
# PORT is injected by Railway automatically.
# DB_PATH should be set to a path inside a Railway persistent volume,
# e.g. DB_PATH=/data/centsaver.db — configure this in Railway's environment
# variable settings after attaching a volume mounted at /data.
ENV PORT=8000
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# start.py reads PORT via os.environ — no shell variable expansion needed.
CMD ["python", "start.py"]
