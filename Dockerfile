FROM node:24-alpine AS dashboard
WORKDIR /frontend
RUN npm install --global pnpm@11.19.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/news-tool

COPY requirements.lock ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 5 --timeout 60 --upgrade pip==26.2.1

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 5 --resume-retries 10 --timeout 60 -r requirements.lock

RUN useradd --create-home newsroom \
    && mkdir -p /data/assets /data/audio /data/video /data/tmp \
    && chown -R newsroom:newsroom /data

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests
COPY --from=dashboard /frontend/dist ./frontend/dist

USER newsroom

CMD ["python", "-m", "app.start"]
