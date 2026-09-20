FROM python:3.11-slim

WORKDIR /bot
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Health check server port (Koyeb / VPS monitoring)
ENV HEALTH_PORT=8080

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m botuser && mkdir -p /bot/downloads && chown -R botuser:botuser /bot
USER botuser

VOLUME ["/bot/downloads"]
EXPOSE 8080
CMD ["python", "bot.py"]
