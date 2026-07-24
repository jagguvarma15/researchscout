# Web frontend image. Build with apps/web as the context:
#   docker build -f docker/web.Dockerfile apps/web
FROM node:22-alpine AS build
WORKDIR /app
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build && pnpm prune --prod

# The Node adapter bundles app code, but server deps with conditional exports (openid-client,
# redis) are externalized by Vite — production node_modules must ship alongside dist/.
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production HOST=0.0.0.0 PORT=4321
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
EXPOSE 4321
CMD ["node", "dist/server/entry.mjs"]
