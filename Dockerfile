FROM python:3.11-slim
WORKDIR /app

# Install curl for healthchecks (python:3.11-slim does not include it)
RUN apt-get update -qq && apt-get install -y -qq curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p uploads
VOLUME ["/app/data"]
ENV FLASK_ENV=production
EXPOSE 80
CMD ["gunicorn", "-w", "1", "--threads", "4", "--timeout", "300", "-b", "0.0.0.0:80", "app:app"]
