FROM node:26-bookworm-slim AS builder

# Build the Python runtime in an isolated stage so vulnerable build-time package
# layers cannot survive in the shipped runner image.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN ln -sf /usr/bin/python3 /usr/local/bin/python

# Build a pinned upstream Bubblewrap release instead of inheriting Debian
# Bookworm's older 0.8.0 runtime. Setuid support is absent upstream in 0.13.x.
ARG BUBBLEWRAP_VERSION=0.13.1
ARG BUBBLEWRAP_SHA256=0e85c8702248b8dd64144f594147553650cdd0e4c951690345faace79c217c4d
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl xz-utils meson ninja-build pkg-config libcap-dev \
    && curl --fail --silent --show-error --location \
      "https://github.com/containers/bubblewrap/releases/download/v${BUBBLEWRAP_VERSION}/bubblewrap-${BUBBLEWRAP_VERSION}.tar.xz" \
      -o /tmp/bubblewrap.tar.xz \
    && echo "${BUBBLEWRAP_SHA256}  /tmp/bubblewrap.tar.xz" | sha256sum --check --strict - \
    && tar -xJf /tmp/bubblewrap.tar.xz -C /tmp \
    && meson setup /tmp/bubblewrap-build "/tmp/bubblewrap-${BUBBLEWRAP_VERSION}" \
      --prefix=/usr -Dman=disabled -Dtests=false -Dselinux=disabled \
      -Dbash_completion=disabled -Dzsh_completion=disabled \
    && meson compile -C /tmp/bubblewrap-build \
    && meson install -C /tmp/bubblewrap-build \
    && /usr/bin/bwrap --version | grep -F "bubblewrap ${BUBBLEWRAP_VERSION}" \
    && rm -rf /tmp/bubblewrap.tar.xz /tmp/bubblewrap-${BUBBLEWRAP_VERSION} /tmp/bubblewrap-build \
      /var/lib/apt/lists/* /var/cache/apt/*

WORKDIR /build
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/python -m pip install --no-cache-dir -r requirements.txt 'urllib3==2.8.0' 'uv==0.13.6' \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade 'setuptools==84.0.0' \
    && /opt/venv/bin/python -m pip check \
    && /opt/venv/bin/python -m pip uninstall -y setuptools msgpack \
    && /opt/venv/bin/python -m pip check \
    && /opt/venv/bin/python -m pip uninstall -y pip \
    && /opt/venv/bin/python -c 'import fastapi, requests, urllib3, uvicorn; assert urllib3.__version__ == "2.8.0"' \
    && /opt/venv/bin/uv --version
FROM node:26-bookworm-slim

# Keep Debian security packages current, then remove package metadata/cache.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends python3 git curl ca-certificates bubblewrap tini \
    && apt-get upgrade -y \
    && dpkg-query -W -f='${binary:Package}\n' | grep -E '^python3-(pip|pip-whl|setuptools|setuptools-whl|urllib3|msgpack)(:|$)' | xargs -r apt-get purge -y \
    && ! dpkg-query -W -f='${binary:Package}\n' | grep -E '^python3-(pip|pip-whl|setuptools|setuptools-whl|urllib3|msgpack)(:|$)' \
    && apt-get autoremove -y --purge \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN ln -sf /usr/bin/python3 /usr/local/bin/python

# Keep the runtime language tooling current and avoid shipping npm's transient cache.
RUN npm install -g npm@12.2.0 pyright@1.1.404 typescript@5.9.3 typescript-language-server@5.0.1 \
    && npm cache clean --force

# npm 12.2.0 still bundles vulnerable transitive copies. Replace only the bundled
# copies with patched releases; this keeps the npm major/runtime contract intact.
RUN mkdir -p /tmp/runtime-fixes \
    && cd /tmp/runtime-fixes \
    && npm install --no-save --no-package-lock --ignore-scripts brace-expansion@5.0.12 undici@6.29.1 \
    && rm -rf /usr/local/lib/node_modules/npm/node_modules/brace-expansion \
    && cp -a node_modules/brace-expansion /usr/local/lib/node_modules/npm/node_modules/ \
    && rm -rf /usr/local/lib/node_modules/npm/node_modules/undici \
    && cp -a node_modules/undici /usr/local/lib/node_modules/npm/node_modules/ \
    && rm -rf /tmp/runtime-fixes

RUN useradd --create-home --uid 10001 runner
WORKDIR /app

# Fail closed: production execution must use a real sandbox backend.
ENV RUNNER_SECURITY_LEVEL=strict \
    RUNNER_SANDBOX_MODE=bwrap \
    RUNNER_ALLOW_NETWORK=false

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /usr/bin/bwrap /usr/bin/bwrap
COPY --from=builder /opt/venv/bin/uv /usr/local/bin/uv
COPY . .
RUN python3 -m compileall -q . \
    && /opt/venv/bin/python -c 'import fastapi, requests, urllib3, uvicorn; assert urllib3.__version__ == "2.8.0"' \
    && /usr/local/bin/uv --version \
    && /opt/venv/bin/python -c "from importlib import metadata; assert metadata.version('urllib3') == '2.8.0'; assert not any((d.metadata.get('Name') or '').lower() in {'pip', 'setuptools', 'msgpack'} for d in metadata.distributions())" \
    && ! dpkg-query -W -f='${binary:Package}\n' | grep -E '^python3-(pip|pip-whl|setuptools|setuptools-whl|urllib3|msgpack)(:|$)' \
    && mkdir -p /workspaces \
    && chown -R runner:runner /workspaces /app /opt/venv
USER runner
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["/bin/sh","-c","exec /opt/venv/bin/uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
