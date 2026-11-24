# API

Базовый путь `/api/v1`. OpenAPI доступен по `/docs` на порту backend. Ошибки возвращают `{"error":{"code":"…","message":"…","request_id":"…"}}` и заголовок `X-Request-ID`. Списки поддерживают `offset` и `limit`.

| Метод и маршрут | Действие |
| --- | --- |
| GET /health, /system/status | Liveness и проверка компонентов |
| POST /documents/upload | Multipart `files` (до 25), опционально `encoding`. Результат отдельно для каждого файла |
| GET /documents | Параметры `query`, `format`, `status`, пагинация |
| GET /documents/{id} | Метаданные и последнее задание |
| GET /documents/{id}/file | Оригинал в режиме скачивания |
| GET /documents/{id}/preview | PDF или очищенное HTML |
| GET /documents/{id}/content | Канонические блоки, исходные строки для TXT/MD и предупреждения |
| POST /documents/{id}/reindex | `{"encoding":"windows-1251"}` опционально |
| DELETE /documents/{id} | Скрыть из выдачи и поставить очистку в очередь |
| GET /jobs, /jobs/{id} | Состояние заданий |
| POST /jobs/{id}/retry, /jobs/{id}/cancel | Повторить или отменить |
| POST /search | `query`, `mode`, `top_k`, `filters` |
| POST /chat, /chat/stream | Те же поля, плюс обязательный `provider` и поля генерации |
| GET, POST /chat/sessions | История и создание диалога |
| GET, DELETE /chat/sessions/{id} | Чтение и удаление диалога |
| GET /providers | Доступность и разрешение передачи данных |
| GET, PATCH /settings | Настройки запросов и модель индекса |

Режимы поиска `hybrid`, `dense`, `bm25`. Фильтры `format`, `publication_year`, `language`, `document_ids`. Для диалога передайте `session_id`. Для повторной генерации передайте также `reuse_message_id` и исходный вопрос. Для облака обязательны `provider: "api"` и `cloud_consent: true`.

SSE возвращает события `token` с полем `text` и `final` с проверенным ответом, источниками и предупреждениями. Ошибки после начала потока возвращаются событием `error`. Клиент прерывает генерацию разрывом соединения. Пока не получено `final`, ссылки в тексте потока предварительные.

Успешная физическая очистка документа каскадно удаляет служебные записи его заданий. Сохранённая история может содержать ссылки на удалённый документ. Переход по ним возвращает 404.
