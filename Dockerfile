# Use Python 3.10 slim image as base
# Explicitly specify linux/amd64 platform for Cloud Run compatibility
FROM --platform=linux/amd64 python:3.10-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
# COPY frontend/ ./frontend/

# Expose port (Cloud Run uses PORT env var, default 8080)
EXPOSE 8080

# Run uvicorn with production settings
# Cloud Run sets PORT environment variable automatically
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1

