FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY crawler ./crawler
COPY fixtures ./fixtures
COPY jd_rules ./jd_rules
COPY jd_rules_cli.py forum_probe.py README.md .env.example ./

RUN mkdir -p /data /jdownloader/folderwatch && \
    python -m compileall -q crawler jd_rules_cli.py

EXPOSE 8096

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8096/health', timeout=3).read()" || exit 1

CMD ["uvicorn", "crawler.server:app", "--host", "0.0.0.0", "--port", "8096"]
