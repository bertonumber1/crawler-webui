FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CW_DB_PATH=/data/crawler.db \
    CW_FOLDERWATCH=/jdownloader-folderwatch \
    CW_DOWNLOAD_ROOT=/output/_CRAWLER

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin crawler \
    && mkdir -p /data /logs \
    && chown -R crawler:crawler /app /data /logs

USER crawler

EXPOSE 8096

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8096/api/jd', timeout=4)"

CMD ["python", "-m", "uvicorn", "crawler.server:app", "--host", "0.0.0.0", "--port", "8096"]
