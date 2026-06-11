"""
指标采集逻辑模块
负责定期调用 GCP Monitoring API 采集指标，并更新 Prometheus Gauge 对象。

核心设计要点：
1. 采集间隔最小 60 秒，与 GCP 内置指标采样间隔保持一致
2. 查询时间窗口向过去偏移（query_offset），应对 GCP 数据延迟（3~5 分钟）
3. 支持实例自动发现（未指定实例时，扫描最近 10 分钟的指标数据）
4. 所有异常均被捕获并记录日志，避免采集线程崩溃退出
"""

import logging
import time
import threading
from typing import Dict, List, Optional

from .gcp_client import GCPMonitoringClient
from . import metrics

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    GCP 指标采集器

    在独立线程中运行，定期从 GCP Cloud Monitoring API 采集 Cloud SQL 和
    Memorystore 的 CPU 使用率，并写入 Prometheus Gauge 对象。
    """

    def __init__(
        self,
        gcp_client: GCPMonitoringClient,
        project_id: str,
        scrape_interval: int,
        query_offset: int,
        cloudsql_config: Dict,
        memorystore_config: Dict,
    ):
        """
        初始化采集器

        Args:
            gcp_client:          GCP Monitoring API 客户端实例
            project_id:          GCP 项目 ID
            scrape_interval:     采集间隔（秒），最小值 60
            query_offset:        查询时间偏移（秒），应对 GCP 数据延迟
            cloudsql_config:     Cloud SQL 配置节（enabled, instances）
            memorystore_config:  Memorystore 配置节（enabled, instances）
        """
        self.gcp_client = gcp_client
        self.project_id = project_id
        self.scrape_interval = max(60, int(scrape_interval))  # 强制最小 60 秒
        self.query_offset = int(query_offset)
        self.cloudsql_config = cloudsql_config
        self.memorystore_config = memorystore_config

        # 缓存已发现的实例列表（自动发现模式下使用）
        self._discovered_cloudsql: List[str] = []
        self._discovered_memorystore: List[str] = []

        # 用于控制采集线程
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        logger.info(
            f"采集器初始化完成：project={project_id}, "
            f"interval={self.scrape_interval}s, offset={self.query_offset}s"
        )

    # -------------------------------------------------------------------------
    # 线程控制
    # -------------------------------------------------------------------------

    def start(self):
        """启动采集线程，立即执行一次采集后进入定时循环"""
        if self._thread is not None:
            logger.warning("采集线程已在运行，忽略重复启动")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="gcp-collector", daemon=True
        )
        self._thread.start()
        logger.info("采集线程已启动")

    def stop(self):
        """通知采集线程停止，并等待其退出"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("采集线程已停止")

    def _run_loop(self):
        """采集主循环：执行一次 → 休眠 scrape_interval → 重复"""
        logger.info("采集主循环开始")
        # 先执行一次自动发现（若启用）
        self._refresh_discovery()

        while not self._stop_event.is_set():
            try:
                self.update_metrics()
            except Exception as e:
                logger.error(f"采集过程出现未预期异常: {e}", exc_info=True)
                metrics.SCRAPE_SUCCESS.set(0)

            # 使用 Event.wait 代替 time.sleep，可被 stop() 立即中断
            self._stop_event.wait(timeout=self.scrape_interval)

        logger.info("采集主循环结束")

    # -------------------------------------------------------------------------
    # 实例自动发现
    # -------------------------------------------------------------------------

    def _refresh_discovery(self):
        """刷新自动发现的实例列表（仅在实例列表为空时触发）"""
        cloudsql_instances = self.cloudsql_config.get("instances", []) or []
        memorystore_instances = self.memorystore_config.get("instances", []) or []

        if self.cloudsql_config.get("enabled", True) and not cloudsql_instances:
            logger.info("正在自动发现 Cloud SQL 实例...")
            self._discovered_cloudsql = self.gcp_client.discover_cloudsql_instances()

        if self.memorystore_config.get("enabled", True) and not memorystore_instances:
            logger.info("正在自动发现 Memorystore 实例...")
            self._discovered_memorystore = self.gcp_client.discover_memorystore_instances()

    # -------------------------------------------------------------------------
    # 指标更新
    # -------------------------------------------------------------------------

    def update_metrics(self):
        """
        执行一次完整的指标采集

        流程：
        1. 记录开始时间
        2. 采集 Cloud SQL CPU（若启用）
        3. 采集 Memorystore CPU（若启用）
        4. 更新 Exporter 自身运行指标（耗时、成功标志、时间戳）
        """
        start_time = time.monotonic()
        success = True

        try:
            # Cloud SQL
            if self.cloudsql_config.get("enabled", True):
                cloudsql_ok = self._collect_cloudsql()
                success = success and cloudsql_ok

            # Memorystore
            if self.memorystore_config.get("enabled", True):
                memorystore_ok = self._collect_memorystore()
                success = success and memorystore_ok

        except Exception as e:
            logger.error(f"指标更新异常: {e}", exc_info=True)
            success = False

        elapsed = time.monotonic() - start_time
        metrics.SCRAPE_DURATION_SECONDS.set(round(elapsed, 4))
        metrics.SCRAPE_SUCCESS.set(1 if success else 0)
        metrics.SCRAPE_TIMESTAMP.set(time.time())

        logger.debug(f"本次采集耗时 {elapsed:.2f}s，成功: {success}")

    def _collect_cloudsql(self) -> bool:
        """
        采集 Cloud SQL CPU 使用率

        Returns:
            是否采集成功
        """
        try:
            configured_instances = self.cloudsql_config.get("instances", []) or []
            # 若配置了具体实例则使用配置，否则使用自动发现结果
            instances = configured_instances if configured_instances else self._discovered_cloudsql

            time_series_list = self.gcp_client.query_cloudsql_cpu(
                offset_seconds=self.query_offset,
                instances=configured_instances,  # 传入配置实例，空列表则不过滤
            )

            if not time_series_list:
                logger.warning("Cloud SQL: 未获取到任何时间序列数据")
                # 若自动发现列表为空，尝试刷新
                if not configured_instances and not self._discovered_cloudsql:
                    self._discovered_cloudsql = self.gcp_client.discover_cloudsql_instances()
                metrics.CLOUDSQL_INSTANCE_COUNT.set(0)
                return True  # 空结果不算失败

            instance_count = 0
            for ts in time_series_list:
                labels = ts.resource.labels
                # database_id 格式为 "project_id:instance_name"
                db_id = labels.get("database_id", "")
                instance_name = db_id.split(":", 1)[1] if ":" in db_id else db_id
                region = labels.get("region", "unknown")

                # 取最近一个对齐后的数据点（points[0] 是最新的）
                value = self._extract_point_value(ts)
                if value is not None:
                    metrics.CLOUDSQL_CPU_UTILIZATION.labels(
                        project=self.project_id,
                        instance=instance_name,
                        region=region,
                    ).set(round(value, 6))
                    instance_count += 1
                    logger.debug(
                        f"Cloud SQL [{instance_name}] CPU: {value * 100:.2f}%"
                    )

            metrics.CLOUDSQL_INSTANCE_COUNT.set(instance_count)
            logger.info(f"Cloud SQL: 成功更新 {instance_count} 个实例的 CPU 指标")
            return True

        except Exception as e:
            logger.error(f"采集 Cloud SQL 指标失败: {e}", exc_info=True)
            return False

    def _collect_memorystore(self) -> bool:
        """
        采集 Memorystore (Redis) CPU 使用率

        Returns:
            是否采集成功
        """
        try:
            configured_instances = self.memorystore_config.get("instances", []) or []
            instances = (
                configured_instances
                if configured_instances
                else self._discovered_memorystore
            )

            time_series_list = self.gcp_client.query_memorystore_cpu(
                offset_seconds=self.query_offset,
                instances=configured_instances,
            )

            if not time_series_list:
                logger.warning("Memorystore: 未获取到任何时间序列数据")
                if not configured_instances and not self._discovered_memorystore:
                    self._discovered_memorystore = (
                        self.gcp_client.discover_memorystore_instances()
                    )
                metrics.MEMORystore_INSTANCE_COUNT.set(0)
                return True

            instance_count = 0
            for ts in time_series_list:
                labels = ts.resource.labels
                instance_id = labels.get("instance_id", "unknown")
                region = labels.get("location", "unknown")

                value = self._extract_point_value(ts)
                if value is not None:
                    metrics.MEMORystore_CPU_UTILIZATION.labels(
                        project=self.project_id,
                        instance=instance_id,
                        region=region,
                    ).set(round(value, 6))
                    instance_count += 1
                    logger.debug(
                        f"Memorystore [{instance_id}] CPU: {value * 100:.2f}%"
                    )

            metrics.MEMORystore_INSTANCE_COUNT.set(instance_count)
            logger.info(f"Memorystore: 成功更新 {instance_count} 个实例的 CPU 指标")
            return True

        except Exception as e:
            logger.error(f"采集 Memorystore 指标失败: {e}", exc_info=True)
            return False

    # -------------------------------------------------------------------------
    # 工具方法
    # -------------------------------------------------------------------------

    @staticmethod
    def _extract_point_value(time_series) -> Optional[float]:
        """
        从 TimeSeries 中提取最新数据点的值

        TimeSeries.points[0] 是对齐后的最新数据点。
        CPU 使用率为 DOUBLE 类型，但也做兼容处理以应对异常数据。

        Args:
            time_series: GCP TimeSeries 对象

        Returns:
            浮点数值，若无法提取则返回 None
        """
        if not time_series.points:
            return None

        point = time_series.points[0]
        value = point.value

        try:
            # DOUBLE 类型（CPU 指标的常见类型）
            if value.double_value != 0.0:
                return value.double_value
            # INT64 类型兼容
            if value.int64_value != 0:
                return float(value.int64_value)
            # 值确实为 0 的情况
            return value.double_value if value.double_value == 0.0 else float(value.int64_value)
        except Exception as e:
            logger.warning(f"提取数据点值失败: {e}，value_type={type(value)}")
            return None
