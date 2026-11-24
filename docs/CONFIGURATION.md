# Конфигурация

Значения по умолчанию находятся в [configs/default.yaml](../configs/default.yaml). Скопируйте [пример .env](../.env.example) и переопределите параметры окружения. Настройки модели, версии индекса, размеров и таймаутов валидируются при старте.

| Параметр | Назначение |
| --- | --- |
| TRAG_DATA, TRAG_CONFIG | Корень состояния и YAML |
| TRAG_QDRANT_URL, TRAG_EMBED_URL | Внутренние адреса index и inference |
| TRAG_EMBED_PATH, TRAG_EMBED_DEVICE | Локальный snapshot embeddings и устройство |
| TRAG_DOCLING_ARTIFACTS | Каталог локальных артефактов PDF |
| TRAG_SOFFICE, TRAG_PARSER_SOCKET | Бинарный файл LibreOffice и Unix socket процесса парсинга |
| TRAG_LOCAL_URL, TRAG_LOCAL_MODEL | Сервер llama.cpp и alias модели |
| TRAG_CLOUD_ALLOWED | Разрешение сервера на отправку контекста |
| TRAG_GIGACHAT_CREDENTIALS, MODEL, SCOPE, CA_FILE | Переменные с полным префиксом `TRAG_GIGACHAT_` для OAuth, модели, тарифа и CA |
| TRAG_ENVIRONMENT, TRAG_USER, TRAG_PASSWORD | Режим запуска и Basic Auth в production |

В YAML контролируются лимиты файлов и распаковки, timeout парсинга, OCR, устройство Docling, batch size embeddings, параметры retrieval, окно генерации и аренда задания. Чанкинг фиксирован 512/80. Любое несовместимое изменение формирует новый идентификатор коллекции.

Default top-k 8, dense и BM25 берут до 20 кандидатов, initial RRF k=60. Смена LLM, Temperature и параметры поиска изменяются в интерфейсе и применяются к следующим запросам без перестроения.

Окно llama.cpp задайте равным `generation.local_window`. Не уменьшайте контекст сервера отдельно без изменения конфигурации backend. Секреты хранятся только на сервере.
