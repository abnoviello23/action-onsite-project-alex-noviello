FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# postgresql-client gives the migrate/reset jobs psql and pg_isready.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY src/ ./src/

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

CMD ["python", "-c", "print('specify a command')"]
