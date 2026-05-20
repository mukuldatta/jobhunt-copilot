FROM python:3.11-slim

WORKDIR /app

# System deps needed by Playwright's Chromium
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium for Playwright (Linux — no sandbox issues)
RUN playwright install chromium && playwright install-deps chromium

COPY backend/ ./backend/
COPY resume/ ./resume/

WORKDIR /app/backend
EXPOSE 8000

# Railway injects $PORT; fall back to 8000 for local Docker runs
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
