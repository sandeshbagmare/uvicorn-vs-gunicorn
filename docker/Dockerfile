# Linux image so Gunicorn (Unix-only) can run even when you're on Windows.
# Build context is the project root:  docker build -f docker/Dockerfile -t uvg .
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Install deps first for better layer caching.
COPY app/requirements.txt /app/app/requirements.txt
RUN pip install -r /app/app/requirements.txt

COPY app /app/app
COPY benchmarks /app/benchmarks

EXPOSE 8000
# Default command runs Gunicorn + UvicornWorker. Override in docker-compose for the uvicorn variants.
CMD ["python", "-m", "gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", "-w", "4", \
     "-b", "0.0.0.0:8000", "--max-requests", "10000", "--max-requests-jitter", "1000"]
