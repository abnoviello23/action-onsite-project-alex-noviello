# BGE-small encoder. Separate from the slim python-service image: torch would
# balloon every poller/worker replica, and ingest does not load the model.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

COPY docker/embed-requirements.txt ./
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r embed-requirements.txt

COPY src/ ./src/

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /models \
    && chown -R app:app /app /models
USER app

EXPOSE 8080
CMD ["python", "-m", "embed"]
