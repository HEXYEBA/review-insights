# Credentials are created only at runtime in a separate named volume.
FROM node:22-bookworm-slim AS codex
RUN npm install --global @openai/codex@0.160.1 && npm cache clean --force

FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=0 \
    CODEX_HOME=/home/app/.codex \
    CODEX_BIN=/usr/local/bin/codex \
    ARTIFACTS_DIR=/app/artifacts \
    MODEL_NAME=gpt-6.1-sol
WORKDIR /app
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /usr/local/lib/node_modules/@openai/codex /opt/codex
RUN ln -s /opt/codex/bin/codex.js /usr/local/bin/codex
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && apt-get update \
    && apt-get install --no-install-recommends -y xvfb xauth \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.codex /app/artifacts /app/.browser-profile/yandex \
    && chown -R app:app /home/app /app/artifacts /app/.browser-profile
# Explicit allowlist: no host credentials, reports or browser profiles are copied.
COPY app.py pytest.ini ./
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY review_insights/ ./review_insights/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
USER app
EXPOSE 8502
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8502/_stcore/health', timeout=2)"
ENTRYPOINT ["xvfb-run", "-a", "--server-args=-screen 0 1440x1000x24", "--"]
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8502", "--server.headless=true", "--browser.gatherUsageStats=false"]
