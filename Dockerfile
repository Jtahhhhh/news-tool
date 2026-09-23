FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/news-tool
COPY requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --retries 5 --timeout 60 --upgrade pip==26.2.1
RUN --mount=type=cache,target=/root/.cache/pip --mount=type=bind,source=.wheels,target=/wheels \
    if test -f /wheels/READY; then \
      pip install --no-index --find-links=/wheels -r requirements.lock; \
    else \
      pip install --retries 5 --resume-retries 10 --timeout 60 -r requirements.lock; \
    fi
RUN useradd --create-home newsroom
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests
USER newsroom
CMD ["python", "-m", "app.start"]
