# 部署到 Zeabur（或任何吃 Dockerfile 的平台）用。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY knowledge ./knowledge
COPY scripts ./scripts

# 產出資料夾。注意：容器重啟後檔案會消失，
# 部署版建議把 DEFAULT_DESTINATION 設成 drive，讓產出直接進 Google 雲端硬碟。
RUN mkdir -p outputs data

ENV HOST=0.0.0.0 \
    PORT=8080 \
    DEFAULT_DESTINATION=drive

EXPOSE 8080

CMD ["python", "-m", "app"]
