FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects $PORT at runtime (default 8080)
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "bot.main"]
