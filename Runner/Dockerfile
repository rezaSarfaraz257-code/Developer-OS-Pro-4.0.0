FROM node:22-bookworm-slim AS builder

# Build the Python runtime in an isolated stage so vulnerable build-time package
# layers cannot survive in the shipped runner image.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN ln -sf /usr/bin/python3 /usr/local/bin/python

WORKDIR /build
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/python -m pip install --no-cache-dir -r requirements.txt \
    && (/opt/venv/bin/python -m pip uninstall -y setuptools urllib3 msgpack || true) \
    && /opt/venv/bin/python -m pip install --no-cache-dir 'setuptools==78.1.1' urllib3==2.8.0 msgpack==1.2.1 \
    && find /opt/venv/lib/python3.11/site-packages -maxdepth 1 -type d \\( -name 'msgpack-*.dist-info' ! -name 'msgpack-1.2.1.dist-info' -o -name 'msgpack-*.egg-info' ! -name 'msgpack-1.2.1.egg-info' -o -name 'setuptools-*.dist-info' ! -name 'setuptools-78.1.1.dist-info' -o -name 'setuptools-*.egg-info' ! -name 'setuptools-78.1.1.egg-info' -o -name 'urllib3-*.dist-info' ! -name 'urllib3-2.8.0.dist-info' -o -name 'urllib3-*.egg-info' ! -name 'urllib3-2.8.0.egg-info' \\) -prune -exec rm -rf '{}' + \\
    && /opt/venv/bin/python -c 'from importlib.metadata import distributions; expected={\"msgpack\":\"1.2.1\",\"setuptools\":\"78.1.1\",\"urllib3\":\"2.8.0\"}; found={k:[] for k in expected}; [(found[d.metadata[\"Name\"].lower().replace(\"_\",\"-\")].append(d.version)) for d in distributions() if d.metadata.get(\"Name\",\"\").lower().replace(\"_\",\"-\") in found]; assert found == {\"msgpack\":[\"1.2.1\"],\"setuptools\":[\"78.1.1\"],\"urllib3\":[\"2.8.0\"]}, found' \\\n    && /opt/venv/bin/python -m pip check \
    && /opt/venv/bin/python -c 'import setuptools, urllib3, msgpack; assert tuple(map(int, setuptools.__version__.split(".")[:2])) >= (78, 1); assert urllib3.__version__ == "2.8.0"; assert msgpack.__version__ == "1.2.1"' \
    && /opt/venv/bin/python -c 'from importlib.metadata import version; assert version("setuptools") >= "78.1.1"; assert version("urllib3") == "2.8.0"; assert version("msgpack") == "1.2.1"'

FROM node:22-bookworm-slim

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
    && npm install --no-save --no-package-lock --ignore-scripts brace-expansion@5.0.12 undici@6.28.1 \
    && rm -rf /usr/local/lib/node_modules/npm/node_modules/brace-expansion \
    && cp -a node_modules/brace-expansion /usr/local/lib/node_modules/npm/node_modules/ \
    && rm -rf /usr/local/lib/node_modules/npm/node_modules/undici \
    && cp -a node_modules/undici /usr/local/lib/node_modules/npm/node_modules/ \
    && rm -rf /tmp/runtime-fixes

RUN useradd --create-home --uid 10001 runner
WORKDIR /app

# Fail closed: production execution must use a real sandbox backend.
ENV RUNNER_SECURITY_LEVEL=strict \
    RUNNER_SANDBOX_MODE=container \
    RUNNER_ALLOW_NETWORK=false

COPY --from=builder /opt/venv /opt/venv
COPY . .
RUN python3 -m compileall -q . \
    && /opt/venv/bin/python -m pip check \
    && /opt/venv/bin/python -c 'import setuptools, urllib3, msgpack; assert tuple(map(int, setuptools.__version__.split(".")[:2])) >= (78, 1); assert urllib3.__version__ == "2.8.0"; assert msgpack.__version__ == "1.2.1"' \
    && mkdir -p /workspaces \
    && chown -R runner:runner /workspaces /app /opt/venv

USER runner
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["/bin/sh","-c","exec /opt/venv/bin/uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
