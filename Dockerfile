FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py crawl.py game_page.py letsplay.py listing.py similar.py store.py summary.py translate.py ./
COPY templates ./templates
COPY static ./static

ENV CATALOG_DB=/data/catalog.db
VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
