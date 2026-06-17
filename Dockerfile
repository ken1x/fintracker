# Используем официальный легкий образ Python
FROM python:3.12-slim

# Устанавливаем системные зависимости для сборки библиотек (Pandas, psycopg2)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Задаем рабочую директорию внутри контейнера
WORKDIR /app

# Запрещаем Python писать .pyc файлы и буферизовать вывод (чтобы логи шли в консоль сразу)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Копируем файл зависимостей и устанавливаем их
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Копируем весь код проекта внутрь контейнера
COPY . /app/