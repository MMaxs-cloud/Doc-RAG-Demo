# Лицензии сторонних компонентов

| Компонент | Условия по данным upstream |
| --- | --- |
| FastAPI, React, Vite, Docling | MIT. Для отдельных весов Docling нужно проверять model cards. [Docling](https://github.com/docling-project/docling/blob/main/LICENSE) |
| Giga Embeddings 480M | MIT по официальной карточке. [Giga Embeddings](https://huggingface.co/ai-sage/Giga-Embeddings-instruct-480M-0826) |
| GigaChat 3.1 Lightning GGUF | MIT по официальной карточке. [Lightning](https://huggingface.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF) |
| llama.cpp | MIT. [Лицензия llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE) |
| Qdrant, PDF.js | Apache-2.0. [Qdrant](https://github.com/qdrant/qdrant/blob/master/LICENSE), [PDF.js](https://github.com/mozilla/pdf.js/blob/master/LICENSE) |
| PyMuPDF | AGPL-3.0 или коммерческая лицензия. Для закрытого продукта рассмотрите коммерческую лицензию Artifex. Возможная альтернативная реализация резервного извлечения через pypdfium2 потребует отдельного сравнения и изменения требования. [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/about.html#license) |
| LibreOffice | MPL-2.0, для отдельных частей действуют иные указанные upstream лицензии. [LibreOffice](https://www.libreoffice.org/about-us/licenses/) |
| GigaChat API | Условия договора и тарифа. Коммерческое использование требует подходящего scope. [Документация GigaChat](https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/gigachat-api) |

Транзитивные версии зафиксированы в `backend/uv.lock` и `frontend/package-lock.json`. Перед распространением проверьте notices зависимостей и объём условий AGPL или коммерческого разрешения. Веса скачиваются отдельно и в репозиторий не входят. Демонстрационный корпус составлен специально для проекта.
