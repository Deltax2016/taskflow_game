# --- Этап 1: сборка статики ---
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .

# Vite подставляет VITE_*-переменные на этапе СБОРКИ (в рантайне их менять
# уже нельзя — они запечены в бандл). Поэтому режим приходит build-аргументом,
# а не переменной окружения контейнера.
ARG VITE_GAME_MODE=false
ENV VITE_GAME_MODE=$VITE_GAME_MODE
RUN npm run build

# --- Этап 2: раздача через nginx ---
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
