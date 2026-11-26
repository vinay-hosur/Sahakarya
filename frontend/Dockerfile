FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json .oxlintrc.json ./
COPY public ./public
COPY src ./src
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/default.conf.template
COPY --chmod=755 configure-api-host.sh /docker-entrypoint.d/10-configure-api-host.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
