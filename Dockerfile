# ============================================================
# 构建阶段：安装依赖
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ============================================================
# 运行阶段：最终镜像
# ============================================================
FROM python:3.11-slim

LABEL maintainer="gcp-monitor-exporter"
LABEL description="Prometheus Exporter for GCP Cloud SQL & Memorystore CPU metrics"

# 创建非 root 用户运行
RUN groupadd -r exporter && useradd -r -g exporter exporter

WORKDIR /app

# 从构建阶段复制已安装的 Python 依赖
COPY --from=builder /install /usr/local

# 复制应用代码
COPY main.py .
COPY config.yaml .
COPY exporter/ ./exporter/

# 切换为非 root 用户
USER exporter

# 暴露 Prometheus 指标端口
EXPOSE 9100

# 健康检查：访问 /metrics 端点
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9100/metrics')"

ENTRYPOINT ["python", "main.py"]
CMD ["--config", "/app/config.yaml"]
