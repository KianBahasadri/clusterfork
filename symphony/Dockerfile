FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM node:24-bookworm-slim

ARG TARGETARCH
ARG TERRAFORM_VERSION=1.15.9
LABEL org.opencontainers.image.source="https://github.com/KianBahasadri/clusterfork"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
         file gawk git gh jq libatomic1 lua5.4 patch python3 python3-venv \
         ripgrep shellcheck sqlite3 tini tmux unzip zip \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    cd /tmp; \
    archive="terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH:-amd64}.zip"; \
    base="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}"; \
    curl -fsSLO "$base/$archive"; \
    curl -fsSL "$base/terraform_${TERRAFORM_VERSION}_SHA256SUMS" \
      | awk -v file="$archive" '$2 == file' | sha256sum --check -; \
    unzip "$archive" terraform -d /usr/local/bin; \
    rm "$archive"

RUN set -eux; \
    platform="linux_${TARGETARCH:-amd64}"; \
    manifest="$(curl -fsSL "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/${platform}.json")"; \
    url="$(printf '%s' "$manifest" | jq -r '.url')"; \
    sha512="$(printf '%s' "$manifest" | jq -r '.sha512')"; \
    test -n "$url" && test "$url" != "null"; \
    test -n "$sha512" && test "$sha512" != "null"; \
    cd /tmp; \
    curl -fsSL -o agy.payload "$url"; \
    printf '%s  agy.payload\n' "$sha512" | sha512sum --check -; \
    case "$url" in \
      *.tar.gz*) tar -xzf agy.payload -C /tmp antigravity && mv /tmp/antigravity /usr/local/bin/agy ;; \
      *) mv agy.payload /usr/local/bin/agy ;; \
    esac; \
    rm -f /tmp/agy.payload; \
    chmod 0755 /usr/local/bin/agy; \
    agy --version

COPY --from=uv /uv /usr/local/bin/uv
RUN npm install --global --no-audit --no-fund \
      @opencode/cli@2.3.0 pnpm@11.6.0 mermaid@12.3.0 \
    && npm cache clean --force \
    && opencode --version && pnpm --version

WORKDIR /app
COPY symphony/requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/symphony \
    && uv pip install --python /opt/symphony/bin/python --no-cache -r /app/requirements.txt \
    && uv pip install --python /opt/symphony/bin/python --no-cache playwright==1.65.2 \
    && groupadd -g 10001 symphony && useradd -u 10001 -g 10001 -M -d /data/symphony/home symphony

RUN PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
      /opt/symphony/bin/python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright-browsers \
    && rm -rf /var/lib/apt/lists/*
COPY symphony /app/symphony
RUN cp "$(npm root -g)/mermaid/dist/mermaid.min.js" /app/symphony/web/mermaid.min.js \
    && chmod 0644 /app/symphony/web/mermaid.min.js
COPY agents/AGENTS.md /app/agents/AGENTS.md

ARG SYMPHONY_COMMIT=""
ENV PATH="/opt/symphony/bin:$PATH" \
    PYTHONPATH="/app" \
    HOME="/data/symphony/home" \
    PLAYWRIGHT_BROWSERS_PATH="/opt/playwright-browsers" \
    SYMPHONY_COMMIT="${SYMPHONY_COMMIT}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["python", "-m", "symphony"]
