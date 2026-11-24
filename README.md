# Technical RAG 0.6

Веб-приложение для гибридного поиска по инженерному корпусу и генерации ответов с проверенными ссылками на исходные фрагменты. PDF, DOC, DOCX, TXT, Markdown и HTML поступают в одну коллекцию чанков Qdrant. В режиме поиска LLM не требуется.

## Запуск на Windows

Из корня проекта в PowerShell:

```powershell
.\scripts\check.ps1
.\scripts\install.ps1
.\.venv\Scripts\python.exe scripts\models.py --docling --lightning
```

Укажите в `.env` пути к официальным `qdrant.exe` версии 1.20.2 и `llama-server.exe` сборки b12669 через `TRAG_QDRANT_EXE` и `TRAG_LLAMA_EXE`. Подробности Windows, CUDA, Docling и LibreOffice приведены в [инструкции установки](docs/INSTALLATION_WINDOWS.md).

```powershell
.\scripts\start.ps1
```

Откройте [Technical RAG](http://127.0.0.1:5173), выберите «Документы» и загрузите файлы из [демонстрационного корпуса](tests/fixtures/README.md). После появления статуса «Готов» перейдите в чат. Для запуска без локальной LLM используйте `-WithoutLocalModel`. Остановка через `scripts/stop.ps1`.

Для DOC на машине разработки нужен отдельно изолированный LibreOffice. Контейнерная конфигурация обеспечивает сетевую изоляцию парсеров. [Развёртывание Docker Compose](docs/DEPLOYMENT.md).

## Документация

- [Текущее состояние](IMPLEMENTATION_STATUS.md), [отчёт о проверке](docs/REPORT.md) и [демонстрация](docs/DEMO.md).
- [Архитектура](docs/ARCHITECTURE.md), [модель данных](docs/DATA_MODEL.md), [API](docs/API.md), [парсинг](docs/PARSING.md).
- [Конфигурация](docs/CONFIGURATION.md), [модели](docs/MODELS.md), [безопасность](docs/SECURITY.md) и [лицензии](THIRD_PARTY_LICENSES.md).
- [Пользовательское руководство](docs/USER_GUIDE.md), [тесты](docs/TESTING.md), [резервное копирование](docs/BACKUP_RESTORE.md), [ограничения](docs/KNOWN_LIMITATIONS.md).
