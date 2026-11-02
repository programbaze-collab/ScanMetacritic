# Каталог игр с Metacritic

Каталог читает страницы Metacritic, хранит карточки в SQLite и показывает список, карточку и страницу обхода. Время обхода считается по Asia/Novosibirsk.

Публичный адрес: https://metacritic.81-85-78-242.sslip.io/

Переписка по этой задаче: https://raw.githubusercontent.com/programbaze-collab/ScanMetacritic/master/conversation.jsonl

## Запуск

```bash
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Обход вручную и по расписанию:

```bash
python -m crawl run
python -m crawl schedule
```

База по умолчанию: `data/catalog.db`. Каталог создаётся при первом запуске. Файл базы не входит в репозиторий и не подменяется при обновлении кода.

Переменные окружения перечислены в `.env.example`. Без `SUMMARY_API_KEY` пересказ — это первые цитаты со страницы, а вывод по летсплею остаётся пустым.

## Docker

```bash
docker build -t scanmetacritic .
docker run --rm -p 8000:8000 -v scanmetacritic-data:/data scanmetacritic
```

Том `/data` хранит `catalog.db`. Новый образ не затирает уже смонтированную базу.
