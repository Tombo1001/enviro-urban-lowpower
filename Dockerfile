# Build the web page.
FROM node:24-slim AS web
WORKDIR /src
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# The server: Python standard library, plus Pillow for the optional photos.
FROM python:3.15-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data STATIC_DIR=/app/static FIRMWARE_DIR=/firmware PORT=8870
COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt \
    && useradd --uid 1000 --create-home enviro && mkdir /data && chown enviro /data
COPY app/*.py /app/
COPY firmware/ /firmware/
COPY --from=web /src/dist /app/static
USER enviro
WORKDIR /app
VOLUME /data
EXPOSE 8870
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/overview' % os.environ.get('PORT','8870'), timeout=4)"
CMD ["python", "server.py"]
