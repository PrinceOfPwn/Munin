FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git nmap curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY munin ./munin
RUN pip install --no-cache-dir .
COPY soul ./soul
COPY scripts ./scripts

CMD ["python", "-m", "munin.mcp.main", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8890"]
