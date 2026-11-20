FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM node:24-bookworm-slim

ARG TARGETARCH
ARG TERRAFORM_VERSION=1.15.9
LABEL org.opencontainers.image.source="https://github.com/KianBahasadri/clusterfork"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git gh \
         lua5.4 python3 python3-venv shellcheck tini unzip \
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

COPY --from=uv /uv /usr/local/bin/uv
RUN npm install --global --no-audit --no-fund \
      @openai/codex@0.171.0 @opencode/cli@2.3.0 pnpm@11.6.0 \
    && npm cache clean --force \
    && codex --version && opencode --version && pnpm --version

WORKDIR /app
COPY symphony/requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/symphony \
    && uv pip install --python /opt/symphony/bin/python --no-cache -r /app/requirements.txt \
    && groupadd -g 10001 symphony && useradd -u 10001 -g 10001 -M -d /data/symphony/home symphony
COPY symphony /app/symphony

ENV PATH="/opt/symphony/bin:$PATH" \
    HOME="/data/symphony/home" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["python", "-m", "symphony"]
