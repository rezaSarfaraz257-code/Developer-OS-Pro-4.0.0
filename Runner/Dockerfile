FROM node:22-bookworm-slim

# Keep the runner image current at build time and remove package metadata/cache.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip git curl ca-certificates build-essential bubblewrap tini \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN ln -sf /usr/bin/python3 /usr/local/bin/python \
    && ln -sf /usr/bin/pip3 /usr/local/bin/pip

# Install the language tooling globally, then remove npm's root cache so scanners do not
# inspect transient package archives that are not needed at runtime.
RUN npm install -g pyright@1.1.404 typescript@5.9.3 typescript-language-server@5.0.1 \
    && npm cache clean --force

RUN useradd --create-home --uid 10001 runner
WORKDIR /app

# Fail closed: production execution must use a real sandbox backend.
ENV RUNNER_SECURITY_LEVEL=strict \
    RUNNER_SANDBOX_MODE=container \
    RUNNER_ALLOW_NETWORK=false

COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python3 -m compileall -q .
RUN mkdir -p /workspaces && chown -R runner:runner /workspaces /app

USER runner
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["/bin/sh","-c","exec /opt/venv/bin/uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
