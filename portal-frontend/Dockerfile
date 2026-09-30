# Stage 1: Build
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# Explicitly the development configuration. `npm run build` resolves to the
# `production` configuration in angular.json, which has no fileReplacements and
# therefore reads src/environments/environment.ts instead of the development
# file. That made the local compose stack serve a production bundle, so
# `cacheLocation: 'localstorage'` in environment.development.ts had no effect
# here and every guarded route restarted the PKCE exchange on reload. A local
# stack should not be shipping a production bundle; production deployments
# override this with their own build command.
RUN npx ng build --configuration development

# Stage 2: Serve
FROM nginx:alpine
# Angular's application builder places browser assets in dist/<project>/browser.
COPY --from=build /app/dist/portal-frontend/browser/ /usr/share/nginx/html/

# Add custom Nginx configuration for SPA routing
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]