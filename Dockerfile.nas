# A regular web server for a 64-bit Synology with Container Manager.
# The existing Dockerfile is an AWS Lambda image, not a NAS service.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    MPLCONFIGDIR=/tmp/matplotlib XDG_CACHE_HOME=/tmp/cache
WORKDIR /app
COPY requirements-server.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --only-binary=:all: -r requirements-server.txt \
    && useradd --uid 10001 --create-home manager \
    && mkdir data && chown manager:manager data
COPY main.py manager.yaml solar_config.yaml ./
COPY src/ ./src/
USER manager
EXPOSE 8765
# Monitoring/planning only until commissioning; no --live flag.
CMD ["python", "main.py", "serve", "--host", "0.0.0.0", "--port", "8765"]
