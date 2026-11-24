# Модель данных

SQLite открывается с foreign keys, WAL и busy timeout. Миграция версии 1 создаёт таблицы `documents`, `document_versions`, `indexing_jobs`, `index_versions`, `chat_sessions`, `chat_messages`, `app_settings` и `service_state`.

Оригинал хранится под UUID в `documents/<uuid>/original`. Canonical JSON и preview имеют путь `canonical/<uuid>/<generation>.json` и `previews/<uuid>/<generation>.<ext>`. Исходное имя — метаданные. SQLite хранит один SHA-256 на живой или ожидающий очистки документ.

CanonicalDocument хранит формат, MIME, checksum, размер, пути оригинала и предпросмотра, текст, метаданные, блоки, mapping, парсер и версию, кодировку, предупреждения и timestamps. SourceBlock содержит тип, текст и диапазон `[start_offset, end_offset)` по Unicode-символам Python. Координаты и метаданные заполняются только при наличии. Канонический текст может содержать нормализацию; оригинальные байты доступны отдельно.

Qdrant использует UUID чанка, dense vector и полнотекстовое поле. Payload содержит версию индекса, поколение документа, формат, диапазон, число токенов, текст, digest и координаты. При поиске SQL определяет опубликованное поколение, а resolver повторно проверяет документ и диапазон.

Очередь использует lease, heartbeat, cancellation и отдельный claim UUID на попытку. История хранит показанный ответ, retrieved и cited отдельно. Временные данные обработки находятся в `temporary`; backup включает постоянное состояние.
