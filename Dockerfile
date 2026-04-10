# OpenEye Sidecar — multi-stage build
# Runs the Python FastAPI sidecar with persistent SQLite storage.
#
# Build:  docker build -t openeye/sidecar .
# Run:    docker run -d -p 7770:7770 -v openeye-data:/data -e ANTHROPIC_API_KEY=sk-ant-... openeye/sidecar

FROM python:3.12-slim AS base

WORKDIR /app

# Install Python deps
COPY sidecar/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy sidecar code
COPY sidecar/ ./sidecar/
COPY skills/ ./skills/

# Data volume
ENV OPENEYE_HOME=/data
VOLUME /data

EXPOSE 7770

CMD ["uvicorn", "sidecar.server:app", "--host", "0.0.0.0", "--port", "7770", "--workers", "1"]
