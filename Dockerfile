FROM python:3.12-slim

LABEL maintainer="Updatarr"
LABEL description="Forces Radarr quality profiles from MDBList lists"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Config and DB live in a volume
VOLUME ["/config"]

EXPOSE 7777

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:7777/api/status || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7777"]
