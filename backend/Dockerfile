FROM python:3.12-slim

# Не пишем .pyc, не буферизуем stdout (удобнее логи в docker).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала зависимости — так слой кэшируется и не пересобирается на каждый чих.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Затем код приложения.
COPY app ./app

# База знаний прокидывается volume'ом из docker-compose (см. compose),
# но копируем и в образ, чтобы контейнер работал и без монтирования.
COPY knowledge_base ./knowledge_base

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
