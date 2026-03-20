FROM python:3.12-slim

# System deps for ffmpeg and playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy packages first (for layer caching)
COPY packages/ packages/
RUN pip install --no-cache-dir -e packages/core -e packages/media

# Copy local node
COPY apps/local_node/ apps/local_node/
RUN pip install --no-cache-dir -e apps/local_node

# Data volume
RUN mkdir -p /data/.amplify-os
ENV AMPLIFY_NODE_DATA_DIR=/data/.amplify-os
ENV AMPLIFY_NODE_CACHE_DIR=/data/.amplify-os/cache
ENV AMPLIFY_NODE_MEDIA_OUTPUT_DIR=/data/.amplify-os/media
ENV AMPLIFY_NODE_OFFLINE_DB_PATH=/data/.amplify-os/offline_queue.db

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1

ENTRYPOINT ["amplify-node"]
CMD ["start"]
