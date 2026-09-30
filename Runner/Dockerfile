FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip git curl ca-certificates build-essential bubblewrap && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 runner
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY main.py .
RUN mkdir -p /workspaces && chown -R runner:runner /workspaces /app
RUN chmod 0755 /usr/bin/bwrap
USER runner
EXPOSE 8080
CMD ["/opt/venv/bin/uvicorn","main:app","--host","0.0.0.0","--port","8080"]
