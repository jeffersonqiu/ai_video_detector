FROM python:3.12-slim-bookworm

# Install system dependencies: ffmpeg (includes ffprobe), curl for healthchecks
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY backend/pyproject.toml backend/uv.lock ./

# Install Python dependencies (no dev deps, frozen to lock file)
RUN uv sync --frozen --no-dev

# Keep yt-dlp updated — important for Instagram compatibility
RUN uv pip install -U yt-dlp

# Copy application code
COPY backend/ ./

ENV PYTHONUNBUFFERED=1
ENV PORT=8000
EXPOSE 8000

CMD uv run uvicorn main:app --host 0.0.0.0 --port ${PORT}
