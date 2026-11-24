# Docker Compose

Требуются Docker Engine и Compose v2. На Windows включите Linux containers через Docker Desktop. На Linux подготовьте каталоги, доступные UID 10001 для приложения, и отдельный каталог Qdrant.

1. Подготовьте модельные артефакты с помощью `scripts/models.py --docling --lightning`. Они подключаются к контейнерам в режиме чтения.
2. Создайте `data/application/temporary`, `data/qdrant` и `data/ca`. На Linux назначьте UID 10001 владельцем `data/application` и его содержимого. Для существующих данных сохраните резервную копию.
3. Скопируйте `.env.example` в `.env` и задайте `TRAG_USER`, `TRAG_PASSWORD`. Для llama.cpp в Compose уберите dev-значение `TRAG_LOCAL_URL` из .env, чтобы использовался внутренний адрес.
4. Запустите нужный профиль из корня проекта.

```sh
docker compose --env-file .env -f deployment/docker-compose.yml --profile local up --build -d
```

Для GPU добавьте `-f deployment/docker-compose.gpu.yml` перед `--profile`. Требуются NVIDIA Container Toolkit на Linux или GPU support Docker Desktop. Для облачной генерации запустите Compose без профиля local. Укажите коммерческий scope, имя модели, ключ доступа и `TRAG_CLOUD_ALLOWED=true`. При необходимости добавьте доверенный CA в `data/ca` и задайте `TRAG_DEPLOY_CA_FILE=/ca/ca.pem`.

HTTP frontend слушает только loopback. Для удалённого доступа разместите за TLS reverse proxy с сохранением Host и поддержкой SSE. Qdrant, embeddings и llama.cpp находятся во внутренней сети. Parser имеет `network_mode: none` и принимает задания через Unix socket. Контейнер parser содержит LibreOffice, OCR и системные шрифты.

Состояние приложения в `data/application`, Qdrant в `data/qdrant`. Модели хранятся независимо. [Резервное копирование и восстановление](BACKUP_RESTORE.md). Фактический статус запуска указан в [статусе реализации](../IMPLEMENTATION_STATUS.md).
