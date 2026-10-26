# Web image: static React build served by unprivileged nginx, which also proxies /api to the API.
FROM node:24-alpine AS build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
COPY docs/PROJECT_OVERVIEW.md /src/docs/PROJECT_OVERVIEW.md
COPY scenarios/district.json /src/scenarios/district.json
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30-alpine
# Pick up OS security fixes published after the base image was built.
USER root
RUN apk upgrade --no-cache
USER nginx
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/frontend/dist /usr/share/nginx/html
EXPOSE 8080
