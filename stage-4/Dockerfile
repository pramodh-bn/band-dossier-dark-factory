# Dossier, stage 4: a containerised HTTP service with a browser product.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STATE_PATH=/data/state.json

WORKDIR /app

# Runtime assets are installed here, at build time: no dependency is fetched at run time.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# The state file lives on the container's filesystem, so an abrupt stop and a
# restart answer exactly as the service did before it (§3, Restarts).
RUN mkdir -p /data

EXPOSE 8080
CMD ["python", "-m", "app"]
