FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 10001 dailynews \
    && useradd --uid 10001 --gid dailynews --no-create-home dailynews
COPY daily_news ./daily_news
COPY main.py .
USER dailynews
ENTRYPOINT ["python", "/app/main.py", "--config", "/config/config.toml"]
CMD ["schedule", "--send"]
