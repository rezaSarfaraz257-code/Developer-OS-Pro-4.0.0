FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip git curl ca-certificates build-essential bubblewrap tini golang rustc cargo openjdk-17-jdk php-cli ruby perl lua5.4 r-base-core && rm -rf /var/lib/apt/lists/*
RUN ln -sf /usr/bin/python3 /usr/local/bin/python && ln -sf /usr/bin/pip3 /usr/local/bin/pip || true
RUN useradd --create-home --uid 10001 runner
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY main.py .
RUN mkdir -p /workspaces && chown -R runner:runner /workspaces /app
USER runner
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["/bin/sh","-c","exec /opt/venv/bin/uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
