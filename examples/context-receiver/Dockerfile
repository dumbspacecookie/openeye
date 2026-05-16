FROM python:3.12-slim

WORKDIR /app

# System deps for SQLite
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# Persisted SQLite goes here. Mount a volume for production.
ENV CONTEXT_RECEIVER_DB=/data/context_receiver.db
VOLUME /data

# Bind 0.0.0.0 inside the container — Fly/Cloud Run handle TLS termination
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
