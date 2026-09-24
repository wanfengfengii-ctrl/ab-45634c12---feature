FROM python:3.11-slim

# 纯标准库实现，无第三方依赖；此环境变量让 python -u 输出即时落盘
ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /srv

COPY app/ ./app/
COPY tests/ ./tests/
COPY verify/ ./verify/

EXPOSE 8000

# 容器内健康检查（slim 镜像无 curl，用标准库）
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request,sys; \
r=urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','8000')+'/healthz',timeout=3); \
sys.exit(0 if r.status==200 else 1)"

CMD ["python", "-m", "app.server"]
