# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /build/requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r /build/requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --create-home --home-dir /app app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY --chown=app:app . /app

RUN mkdir -p /app/data /app/reports /app/logs && chown -R app:app /app/data /app/reports /app/logs

USER app

CMD ["python", "-m", "src.main"]
