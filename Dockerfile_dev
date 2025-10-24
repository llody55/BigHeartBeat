FROM swr.cn-southwest-2.myhuaweicloud.com/llody/python:3.10.2-slim

MAINTAINER llody

ENV PIP_CACHE_DIR /app/.cache

WORKDIR /app

COPY . /app

RUN pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 暴露端口
EXPOSE 5000 5001

# 设置环境变量默认值
ENV LOG_LEVEL=INFO
ENV VICTORIA_METRICS_HOST=192.168.1.227:31689
ENV TIMEOUT_SECONDS=30

CMD ["python3","server.py"]
