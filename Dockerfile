FROM python:3.13
# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    git \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno (JS runtime for yt-dlp EJS challenge solver)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (FastAPI runs on port 8000)
EXPOSE 8000

# Health check for FastAPI endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Workers default to 1; set WEB_CONCURRENCY env var to scale (e.g. 4).
# Note: with multiple workers the Telegram bot must run separately via bot.py
# (Phase 4.4). Single-worker deployments can keep using python3 main.py.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-1}"]
