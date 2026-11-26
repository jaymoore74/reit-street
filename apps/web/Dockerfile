# syntax=docker/dockerfile:1
# Client website image (ADR 0013). Build from the repo root:
#   docker build -f apps/web/Dockerfile -t reitstreet-web apps/web
# Runs as a non-root user on port 3000. Settings come from environment variables
# (see .env.example); the database login comes from Secrets Manager at deploy time
# (deploy/aws/web, docs/runbooks/web-hosting.md).
FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-bookworm-slim AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
# AWS RDS certificate authorities, for TLS to Aurora. Set RS_DB_SSL_CA= (empty) to run the
# image against a local database without TLS.
ADD --chmod=644 https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem /app/certs/rds-global-bundle.pem
ENV RS_DB_SSL_CA=/app/certs/rds-global-bundle.pem
COPY --from=build --chown=node:node /app/.next/standalone ./
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
