# Naming server — metadata authority (FastAPI + SQLite).
# Build context = repo root:  docker build -f naming_server/Dockerfile .
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/naming.db \
    REPLICATION_FACTOR=2

WORKDIR /app

# Install runtime deps first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Only the naming package is needed.
COPY naming_server/ ./naming_server/

# Metadata DB lives on a volume so it survives container restarts.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "naming_server.app:app", "--host", "0.0.0.0", "--port", "8000"]
