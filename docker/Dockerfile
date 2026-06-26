# Stage 1: Build
FROM node:20-alpine AS build

WORKDIR /app

# Copy package files and install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy source code
COPY . .

# Build app
RUN npm run build

# Stage 2: Serve with NGINX
FROM nginx:alpine

# Copy build output to NGINX html folder
COPY --from=build /app/dist /usr/share/nginx/html

# Copy custom nginx config (for SPA routing)
COPY nginx/default.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
