/// <reference types="vite/client" />

// Переменные окружения фронтенда, доступные через import.meta.env.
// Vite подставляет их на этапе сборки, поэтому в образ они попадают
// «запечёнными» — менять их в рантайме нельзя (см. docker-compose: build args).
interface ImportMetaEnv {
  readonly VITE_GAME_MODE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
