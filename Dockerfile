FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py reminders.py ./
COPY static ./static

RUN DATA_DIR=/tmp/index-inbox-build AUTH_PROVIDER=local WEBHOOK_SECRET=build-smoke-test \
    python -c "import app" \
    && rm -rf /tmp/index-inbox-build

RUN useradd --system --uid 1000 --create-home index && mkdir -p /data && chown index:index /data
USER index

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["gunicorn", "--bind=0.0.0.0:8080", "--workers=1", "--threads=4", "--timeout=300", "--access-logfile=-", "app:app"]
