"""
GCP Monitor Exporter 程序入口

启动流程：
1. 解析命令行参数
2. 加载并校验配置文件
3. 初始化 GCP Monitoring API 客户端
4. 注册 CustomCollector 到 Prometheus REGISTRY
5. 启动 HTTP Server 暴露 /metrics 端点
6. 监听信号，优雅退出
"""

import argparse
import logging
import signal
import sys
from pathlib import Path

import yaml
from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY

from exporter.gcp_client import GCPMonitoringClient
from exporter.collector import GCPCollector

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        logging.error(f"配置文件不存在: {config_path}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    gcp_config = config.get("gcp", {})
    if not gcp_config.get("project_id"):
        logging.error("配置错误: gcp.project_id 为必填项，请在 config.yaml 中设置")
        sys.exit(1)

    return config


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="GCP Monitor Exporter - Cloud SQL / Memorystore CPU 指标暴露给 Prometheus"
    )
    parser.add_argument(
        "--config", default="config.yaml", help="配置文件路径，默认: config.yaml"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认: INFO",
    )
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    # 加载配置
    logger.info(f"正在加载配置: {args.config}")
    config = load_config(args.config)

    project_id = config["gcp"]["project_id"]
    port = config.get("port", 9168)
    query_offset = config.get("query_offset", 180)
    cloudsql_config = config.get("cloudsql", {"enabled": True, "instances": []})
    memorystore_config = config.get("memorystore", {"enabled": True, "instances": []})

    # 查询窗口：(now - start_offset, now - end_offset)
    # start_offset 取 query_offset * 3，保证窗口够宽
    # end_offset 取 query_offset // 3，留最小延迟余量
    start_offset = query_offset * 3
    end_offset = max(60, query_offset // 3)

    logger.info(f"GCP 项目: {project_id}")
    logger.info(f"端口: {port} | 查询窗口: (now-{start_offset}s, now-{end_offset}s)")

    # 初始化 GCP 客户端
    try:
        gcp_client = GCPMonitoringClient(project_id=project_id)
    except Exception as e:
        logger.error(f"GCP 客户端初始化失败，请检查认证配置: {e}")
        sys.exit(1)

    # 注册 CustomCollector（每次 Prometheus scrape 时自动调用 collect()）
    collector = GCPCollector(
        gcp_client=gcp_client,
        project_id=project_id,
        cloudsql_config=cloudsql_config,
        memorystore_config=memorystore_config,
        start_offset=start_offset,
        end_offset=end_offset,
    )
    REGISTRY.register(collector)
    logger.info("GCPCollector 已注册到 Prometheus REGISTRY")

    # 启动 HTTP Server
    start_http_server(port)
    logger.info(f"Exporter 已就绪: http://localhost:{port}/metrics")

    # 信号处理：优雅退出
    def signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f"收到信号 {sig_name}，正在退出...")
        REGISTRY.unregister(collector)
        logger.info("Exporter 已停止，再见！")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 主线程阻塞
    logger.info("主线程进入等待状态（Ctrl+C 退出）")
    if hasattr(signal, "pause"):
        signal.pause()
    else:
        # Windows 兼容
        import threading
        threading.Event().wait()


if __name__ == "__main__":
    main()
