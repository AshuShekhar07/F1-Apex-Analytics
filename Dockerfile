# Apex21 backend: one image for the API and the scheduled pipeline.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTF1_CACHE_DIR=/data/fastf1_cache \
    FASTF1_CACHE=/data/fastf1_cache

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home apex21 && mkdir -p /data/fastf1_cache /app/logs && chown -R apex21 /data /app
USER apex21

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=8).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
