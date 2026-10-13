# Apex21 website: built with Vite, served by nginx, which also forwards /api to the API container
# (one address for the site and its data, so no CORS setup is needed).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
