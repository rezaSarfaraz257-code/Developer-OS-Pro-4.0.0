FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf.template
RUN printf '%s\n' '#!/bin/sh' 'set -eu' ': ${BACKEND_PROXY_URL:=https://dos-o91y.onrender.com}' 'sed -i "s|__BACKEND_PROXY_URL__|${BACKEND_PROXY_URL}|g" /etc/nginx/conf.d/default.conf.template' 'mv /etc/nginx/conf.d/default.conf.template /etc/nginx/conf.d/default.conf' 'exec nginx -g "daemon off;"' > /docker-entrypoint-local.sh \
    && chmod +x /docker-entrypoint-local.sh
ENV BACKEND_PROXY_URL=https://dos-o91y.onrender.com
EXPOSE 80
CMD ["/docker-entrypoint-local.sh"]
