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

# PORT and WEB_CONCURRENCY: a host that assigns the port (Render) or has little memory (one
# worker on a 512 MB free instance) sets them; docker compose keeps 8000 and two workers.
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8000'), timeout=8).status == 200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --proxy-headers"]
