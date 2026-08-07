# --- stage 1: build the SPA -------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /build
COPY package.json package-lock.json ./
COPY web/package.json ./web/
RUN npm ci
COPY web ./web
RUN npm run build --workspace web

# --- stage 2: runtime -------------------------------------------------------
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY server/pyproject.toml ./server/
COPY server/app ./server/app
RUN pip install --no-cache-dir -e ./server

COPY --from=web /build/web/dist ./web/dist

# Run as a non-root user; the data directory is the only writable path needed.
RUN useradd --create-home --uid 10001 bingo \
 && mkdir -p /app/server/data && chown -R bingo:bingo /app/server/data
USER bingo

ENV ENVIRONMENT=production HOST=0.0.0.0 PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "server"]
