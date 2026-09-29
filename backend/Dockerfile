# syntax=docker/dockerfile:1
# One image for api, worker and migrations (ADR-0013). Build context: backend/.

FROM python:3.12-slim-bookworm AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
COPY pyproject.toml ./
COPY app ./app
# Install runtime dependencies only: the package itself is removed again and the source is copied
# to /app in the runtime stage (alembic/ and alembic.ini expect the source tree next to them).
RUN pip install . && pip uninstall -y ytlead-backend

FROM python:3.12-slim-bookworm AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YTL_ENV=prod \
    YTL_STORAGE_PATH=/data/storage \
    YTL_LOG_JSON=true
RUN groupadd --system --gid 10001 ytlead \
    && useradd --system --uid 10001 --gid ytlead --home-dir /app --no-create-home ytlead \
    && mkdir -p /data/storage \
    && chown ytlead:ytlead /data/storage
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
USER ytlead
EXPOSE 8000
# Default: API. The worker runs `python -m app.workers`, migrations `alembic upgrade head`
# (see infrastructure/docker-compose.yml).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
