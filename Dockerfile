FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/static/uploads

EXPOSE 8080

CMD ["gunicorn", "--config", "gunicorn.conf.py", "--bind", "0.0.0.0:8080", "aioapp:app"]
