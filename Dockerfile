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
COPY evals ./evals

# 本學期設定的範本。第一次啟動時 config.bootstrap_current_term() 會從
# 這份複製出可編輯的 data/current_term.yaml。沒有它的話部署版一開始
# 讀不到任何當期欄位（不會壞，但幹部得從網頁介面整份重填）。
COPY data/current_term.example.yaml ./data/current_term.example.yaml

RUN mkdir -p outputs data

ENV HOST=0.0.0.0 \
    PORT=8080 \
    DEFAULT_DESTINATION=drive

# 提醒：容器重啟後 outputs/ 與 data/agent.sqlite3 會消失。
#   · 產出落點請設 drive
#   · 平台若有持久化磁碟，把 DB_PATH 指到掛載點，對話紀錄才留得住
#   · **一定要設 APP_ACCESS_TOKEN**，否則任何拿到網址的人都能用

EXPOSE 8080

CMD ["python", "-m", "app"]
