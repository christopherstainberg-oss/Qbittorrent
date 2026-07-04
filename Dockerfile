# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Don't buffer logs; no .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG=/config/config.yaml \
    HOST=0.0.0.0 \
    PORT=8500

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY qbit_sorter ./qbit_sorter
COPY serve.py run.py ./

# Config is provided at runtime via a mounted volume at /config.
VOLUME ["/config"]
EXPOSE 8500

# Healthcheck hits '/' (always 200 once the server is up; does not depend on
# qBittorrent being reachable).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8500')+'/', timeout=4)" || exit 1

# Serve the web UI + API + poll loop. CONFIG/HOST/PORT come from the env above
# (overridable via compose). `exec` so Python (PID 1) receives stop signals and
# shuts the poll loop down cleanly.
CMD ["sh", "-c", "exec python serve.py -c \"$CONFIG\" --host \"$HOST\" --port \"$PORT\""]
