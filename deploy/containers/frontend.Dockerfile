FROM node:22-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
COPY scripts/validate_video_catalog.mjs /app/scripts/validate_video_catalog.mjs
COPY backend/app/data /app/backend/app/data
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6
ARG REVISION=uncommitted
ARG SOURCE_ARCHIVE_SHA256=uncommitted
LABEL org.opencontainers.image.source="https://github.com/Perfecto23/sample-agentcore-launchpad" \
    org.opencontainers.image.revision=$REVISION \
    io.launchpad.source-archive-sha256=$SOURCE_ARCHIVE_SHA256
COPY --from=build /app/frontend/dist /usr/share/nginx/html
COPY deploy/containers/nginx.conf /etc/nginx/nginx.conf
COPY deploy/containers/server.conf.template /etc/nginx/server.conf.template
COPY --chmod=755 deploy/containers/frontend-entrypoint.sh /usr/local/bin/launchpad-frontend
ENV BACKEND_UPSTREAM=backend:8000 TRUSTED_PROXY_CIDR=127.0.0.1/32
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz
ENTRYPOINT ["/usr/local/bin/launchpad-frontend"]
CMD ["nginx", "-g", "daemon off;"]
