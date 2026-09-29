# syntax=docker/dockerfile:1
# Next.js standalone server (ADR-0005, ADR-0013). Build context: frontend/.

FROM node:24-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-bookworm-slim AS build
WORKDIR /app
# The /api/* rewrite target is resolved at build time (next.config.ts) — pass the API origin here.
ARG YTL_API_ORIGIN=http://api:8000
ENV YTL_API_ORIGIN=$YTL_API_ORIGIN \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# server.js does not serve .next/static unless it is copied next to it (the project has no public/).
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
