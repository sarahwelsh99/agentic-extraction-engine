FROM python:3.11-slim

WORKDIR /app

# Install minimal dependencies
RUN pip install --no-cache-dir \
    requests \
    python-dateutil

COPY run_extraction.py /app/run_extraction.py
# Shared with the profiling tools: both sides must split rows identically
COPY records.py /app/records.py

RUN chmod +x /app/run_extraction.py

# Generated code is model-written and untrusted; do not run it as root
RUN useradd --create-home --uid 10001 sandbox
USER sandbox

ENTRYPOINT ["python", "/app/run_extraction.py"]
