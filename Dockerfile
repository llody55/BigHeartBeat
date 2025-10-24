FROM python:3.10.2-slim

LABEL maintainer="llody"

ENV PIP_CACHE_DIR /app/.cache

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
        build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install -r requirements.txt

# 暴露端口
EXPOSE 5000 5001

# 设置环境变量默认值
ENV LOG_LEVEL=INFO
ENV VICTORIA_METRICS_HOST=192.168.1.227:31689
ENV TIMEOUT_SECONDS=30

CMD ["python3","server.py"]
