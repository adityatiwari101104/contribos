FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY . /app
RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "contribos", "mcp"]
