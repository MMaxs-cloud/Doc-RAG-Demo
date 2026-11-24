# Резервное копирование и восстановление

Остановите процессы, которые пишут в SQLite, файловое хранилище и Qdrant. В Docker используйте `docker compose stop`. В Windows используйте `scripts/stop.ps1`. Снимок создаётся offline, поэтому записи разных систем относятся к одному состоянию.

```sh
python scripts/storage.py backup --services-stopped --application data/application --qdrant data/qdrant --output data/backups/technical-rag.zip
python scripts/storage.py restore data/backups/technical-rag.zip --destination data/restored
```

Для нативного запуска передайте `--application data`. Архив содержит SQLite, оригиналы, канонические документы, preview и Qdrant. Веса, .env, runtime и временные файлы хранятся отдельно. Архив включает историю и документы. Управляйте доступом к нему как к данным корпуса.

Восстановление проверяет manifest SHA-256 и целостность SQLite в новой папке. Для проверки запустите тот же Qdrant с `data/restored/qdrant`, установите `TRAG_DATA=data/restored/application` и сравните количество документов и результаты контрольного поиска. Не копируйте файлы поверх работающего приложения. Для Docker замените bind-каталоги после остановки сервисов.
