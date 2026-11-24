# Тестирование

Backend из корня проекта:

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests
```

Для dev-окружения без editable install задайте `PYTHONPATH=backend`. Тесты включают парсинг, кодировки, ZIP validation, 512/80, жизненный цикл задания, эталонные цитаты и backup. Тест DOC требует LibreOffice и разрешённого изолированного режима. Тест real inference не запускается по умолчанию.

```powershell
.\.venv\Scripts\python.exe scripts\run_live.py --qdrant data\tools\qdrant\qdrant.exe --embedding data\models\embeddings --docling data\models\docling
```

Для Word и Lightning добавьте `--soffice <путь>` в изолированной среде и `--llama <путь> --gguf <путь>`. Скрипт запускает изолированное тестовое хранилище Qdrant, проверяет embedding-модель, Dense/BM25/Hybrid, смешанный запрос и полный жизненный цикл доступных форматов. Cloud credentials не используются. Для проверки облака требуется настроенный backend и явное согласие на отправку тестового корпуса.

Frontend:

```sh
cd frontend
npm ci
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

Playwright принимает `TRAG_TEST_BROWSER` как путь к установленному Chromium. Browser E2E использует заглушки HTTP для покрытия переходов frontend, реальный backend и inference проверяются отдельно.
