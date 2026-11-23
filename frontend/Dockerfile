# syntax=docker/dockerfile:1

# ------------------------------------------------------------
# Build: type-check and bundle the React app with Vite
# ------------------------------------------------------------
FROM node:22.22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
RUN npm run build

# ------------------------------------------------------------
# Runtime: static files and an /api proxy, served by nginx as a
# non-root user (port 8080)
# ------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

# Where /api requests go (the backend, without /api), and the largest
# upload accepted (keep >= MAX_FILES_PER_UPLOAD x MAX_UPLOAD_SIZE_MB).
ENV API_UPSTREAM=http://api:8000 \
    MAX_UPLOAD_SIZE=1100m

# nginx's entrypoint fills ${API_UPSTREAM} and ${MAX_UPLOAD_SIZE} in at start.
COPY docker/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO /dev/null http://127.0.0.1:8080/healthz || exit 1
