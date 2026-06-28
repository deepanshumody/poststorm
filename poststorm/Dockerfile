# PostStorm — self-contained image. Regenerates synthetic fixtures at build time
# (deterministic + validated), runs as a non-root user, with a liveness healthcheck.
FROM python:3.12-slim

WORKDIR /app

# fonts-liberation = the Courier/Arial fallbacks the generator resolves on Linux.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY generator/ generator/
# Deterministic synthetic EOBs + ground truth (the build fails if a planted
# recoup doesn't reconcile — the generator's validation gate).
RUN python generator/generate.py
COPY frontend/ frontend/

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
