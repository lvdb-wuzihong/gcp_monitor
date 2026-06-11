"""
GCP Monitor Exporter 程序入口

启动流程：
1. 解析命令行参数（--config 指定配置文件路径，默认 ./config.yaml）
2. 加载并校验配置文件
3. 初始化 GCP Monitoring API 客户端
4. 启动指标采集线程（按 scrape_interval 周期采集）
5. 启动 Prometheus HTTP Server，对外暴露 /metrics 端点
6. 监听 SIGINT / SIGTERM 信号，优雅退出
"""

import argparse
import logging
import signal
import sys
from pathlib import Path

import yaml
from prometheus_client import start_http_server

from exporter.gcp_client import GCPMonitoringClient
from exporter.collector import MetricsCollector

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"


def setup_logging(level: str = "INFO"):
    """配置全局日志格式和级别"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """
    加载 YAML 配置文件并进行必填字段校验

    Args:
        config_path: 配置文件路径

    Returns:
        解析后的配置字典

    Raises:
        SystemExit: 配置缺失必填字段时直接退出
    """
    path = Path(config_path)
    if not path.exists():
        logging.error(f"配置文件不存在: {config_path}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 必填字段校验
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
        description="GCP Monitor Exporter - 将 GCP Cloud SQL / Memorystore 指标暴露给 Prometheus"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径，默认: config.yaml",
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
    scrape_interval = config.get("scrape_interval", 60)
    query_offset = config.get("query_offset", 240)
    port = config.get("port", 9100)
    cloudsql_config = config.get("cloudsql", {"enabled": True, "instances": []})
    memorystore_config = config.get("memorystore", {"enabled": True, "instances": []})

    logger.info(f"GCP 项目: {project_id}")
    logger.info(f"采集间隔: {scrape_interval}s | 查询偏移: {query_offset}s | 端口: {port}")

    # 初始化 GCP 客户端
    try:
        gcp_client = GCPMonitoringClient(project_id=project_id)
    except Exception as e:
        logger.error(f"GCP 客户端初始化失败，请检查认证配置: {e}")
        sys.exit(1)

    # 初始化采集器并启动采集线程
    collector = MetricsCollector(
        gcp_client=gcp_client,
        project_id=project_id,
        scrape_interval=scrape_interval,
        query_offset=query_offset,
        cloudsql_config=cloudsql_config,
        memorystore_config=memorystore_config,
    )
    collector.start()

    # 启动 Prometheus HTTP Server
    logger.info(f"Prometheus HTTP Server 启动中，端口: {port}")
    start_http_server(port)
    logger.info(f"Exporter 已就绪，指标端点: http://localhost:{port}/metrics")

    # 信号处理：优雅退出
    def signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f"收到信号 {sig_name}，开始优雅退出...")
        collector.stop()
        logger.info("Exporter 已停止，再见！")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 主线程阻塞，等待信号
    logger.info("主线程进入等待状态（Ctrl+C 退出）")
    signal.pause() if hasattr(signal, "pause") else threading_wait()


def threading_wait():
    """Windows 平台替代 signal.pause() 的阻塞等待"""
    import threading
    threading.Event().wait()


if __name__ == "__main__":
    main()
