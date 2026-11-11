# memoryd — 自托管跨设备长期记忆服务（FastAPI + SQLite FTS5 + jieba BM25）
# 构建: docker build -t memoryd .
# 运行: 见 docker-compose.yml 或 README
FROM python:3.12-slim

# jieba 与 FTS5 均为纯 Python / SQLite 内建，无系统依赖
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MEMORYD_DB_PATH=/var/lib/memoryd/memoryd.db \
    MEMORYD_LISTEN_HOST=0.0.0.0

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

RUN mkdir -p /var/lib/memoryd \
    && python -c "from app.db import init_db; init_db()"

VOLUME ["/var/lib/memoryd"]
EXPOSE 3233

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:3233/v1/health/ready',timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3233"]
