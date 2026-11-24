# Установка на Windows

1. Установите Python 3.12, Node.js 24 с npm и Git. Запустите `scripts/check.ps1`. Для CUDA нужен драйвер NVIDIA и сборки PyTorch и llama.cpp с поддержкой вашей GPU.
2. Запустите `scripts/install.ps1`. Для машины без Docling можно указать `-NoDocling` и использовать резервное извлечение PDF.
3. Распакуйте [Qdrant v1.20.2](https://github.com/qdrant/qdrant/releases/tag/v1.20.2) и [llama.cpp b12669](https://github.com/ggml-org/llama.cpp/releases/tag/b12669) в пользовательские каталоги. Укажите полные пути к EXE через `TRAG_QDRANT_EXE` и `TRAG_LLAMA_EXE` в .env. Для Qdrant поддерживается путь `data/tools/qdrant/qdrant.exe`.
4. Запустите `.venv/Scripts/python.exe scripts/models.py --docling --lightning`. Весы embeddings и Docling размещаются в `data/models/embeddings` и `data/models/docling`, GGUF в `data/models`.
5. Запустите `scripts/start.ps1` и откройте `http://127.0.0.1:5173`. Для CPU-генерации укажите `-GpuLayers 0`, для поиска без сервера llama.cpp используйте `-WithoutLocalModel`. Остановите процессы через `scripts/stop.ps1`. Логи находятся в `data/run`.

Для проверки CUDA выполните `.venv/Scripts/python.exe -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"`. Если Python-окружение установило CPU wheel, выберите CUDA wheel той же версии PyTorch через официальный индекс PyTorch или используйте CUDA overlay Compose. При необходимости задайте `TRAG_EMBED_DEVICE=cpu`.

Нативный LibreOffice проверяется по PATH, стандартному пути Windows и `TRAG_SOFFICE`. Для недоверенных файлов используйте сетевую изоляцию виртуальной машины или контейнера. Для нативной разработки после блокировки сетевого доступа и ограничения файловой системы процесса можно задать `TRAG_ALLOW_NATIVE_OFFICE=true`. DOCX остаётся доступным в текстовом виде без LibreOffice.
