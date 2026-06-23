# Storage server — raw chunk storage on disk (FastAPI).
# Build context = repo root:  docker build -f storage_server/Dockerfile .
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STORAGE_PORT=9000 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY storage_server/ ./storage_server/

# Chunk files live on a volume so data survives container restarts.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 9000

# STORAGE_PORT is fixed at 9000 inside the network; map it per-replica in compose.
CMD ["uvicorn", "storage_server.main:app", "--host", "0.0.0.0", "--port", "9000"]
