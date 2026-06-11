"""
指标采集逻辑模块 — CustomCollector 模式

每次 Prometheus 抓取时调用 collect()，实时查询 GCP API，
使用数据点的真实时间戳（而非抓取时间）上报指标。

参照 Google 官方示例的核心做法：
- GaugeMetricFamily 动态构建指标
- add_metric 传入数据点的 interval.start_time 作为 timestamp
- 不做 aggregation，取最新原始数据点
"""

import logging
import time

from prometheus_client.core import GaugeMetricFamily

from .gcp_client import GCPMonitoringClient

logger = logging.getLogger(__name__)


class GCPCollector:
    """
    Prometheus CustomCollector

    每次 /metrics 被抓取时，实时查询 GCP Cloud Monitoring API，
    将 Cloud SQL 和 Memorystore 的 CPU 使用率转为 Prometheus 指标输出。
    """

    def __init__(
        self,
        gcp_client: GCPMonitoringClient,
        project_id: str,
        cloudsql_config: dict,
        memorystore_config: dict,
        start_offset: int = 600,
        end_offset: int = 180,
    ):
        """
        Args:
            gcp_client:          GCP Monitoring API 客户端
            project_id:          GCP 项目 ID
            cloudsql_config:     Cloud SQL 配置（enabled, instances）
            memorystore_config:  Memorystore 配置（enabled, instances）
            start_offset:        查询窗口起始偏移（秒），默认 600
            end_offset:          查询窗口结束偏移（秒），默认 180
        """
        self.gcp_client = gcp_client
        self.project_id = project_id
        self.cloudsql_config = cloudsql_config
        self.memorystore_config = memorystore_config
        self.start_offset = start_offset
        self.end_offset = end_offset

    def collect(self):
        """
        Prometheus 每次 scrape 时调用此方法。
        yield 出 GaugeMetricFamily 对象。
        """
        scrape_start = time.monotonic()

        # =====================================================================
        # Cloud SQL CPU
        # =====================================================================
        if self.cloudsql_config.get("enabled", True):
            yield from self._collect_cloudsql()

        # =====================================================================
        # Memorystore CPU
        # =====================================================================
        if self.memorystore_config.get("enabled", True):
            yield from self._collect_memorystore()

        # =====================================================================
        # Exporter 自身指标
        # =====================================================================
        elapsed = time.monotonic() - scrape_start

        duration_metric = GaugeMetricFamily(
            "gcp_monitor_scrape_duration_seconds",
            "Time taken to scrape metrics from GCP",
        )
        duration_metric.add_metric([], round(elapsed, 4))
        yield duration_metric

        success_metric = GaugeMetricFamily(
            "gcp_monitor_scrape_success",
            "Whether the last scrape was successful (1=success, 0=failure)",
        )
        success_metric.add_metric([], 1)
        yield success_metric

    def _collect_cloudsql(self):
        """采集 Cloud SQL CPU 使用率"""
        metric = GaugeMetricFamily(
            "gcp_cloudsql_cpu_utilization",
            "Cloud SQL CPU Utilization (percentage 0-100)",
            labels=["database_id"],
        )

        results = self.gcp_client.query_cloudsql_cpu(
            start_offset=self.start_offset,
            end_offset=self.end_offset,
        )

        count = 0
        for ts in results:
            db_id = ts.resource.labels.get("database_id", "unknown")

            if not ts.points:
                continue

            # points[0] 是最新的数据点
            latest_point = ts.points[0]
            cpu_val = latest_point.value.double_value * 100  # 转为百分比

            # 用数据点的真实时间戳（毫秒），而非抓取时间
            timestamp_ms = int(
                latest_point.interval.start_time.timestamp() * 1000
            )

            metric.add_metric(
                labels=[db_id],
                value=cpu_val,
                timestamp=timestamp_ms,
            )
            count += 1
            logger.debug(f"Cloud SQL [{db_id}] CPU: {cpu_val:.2f}%")

        logger.info(f"Cloud SQL: 输出 {count} 个实例指标")
        yield metric

    def _collect_memorystore(self):
        """采集 Memorystore (Redis) CPU 使用率"""
        metric = GaugeMetricFamily(
            "gcp_memorystore_cpu_utilization",
            "Memorystore Redis CPU Utilization (percentage 0-100)",
            labels=["instance_id"],
        )

        results = self.gcp_client.query_memorystore_cpu(
            start_offset=self.start_offset,
            end_offset=self.end_offset,
        )

        count = 0
        for ts in results:
            instance_id = ts.resource.labels.get("instance_id", "unknown")

            if not ts.points:
                continue

            latest_point = ts.points[0]
            cpu_val = latest_point.value.double_value * 100

            timestamp_ms = int(
                latest_point.interval.start_time.timestamp() * 1000
            )

            metric.add_metric(
                labels=[instance_id],
                value=cpu_val,
                timestamp=timestamp_ms,
            )
            count += 1
            logger.debug(f"Memorystore [{instance_id}] CPU: {cpu_val:.2f}%")

        logger.info(f"Memorystore: 输出 {count} 个实例指标")
        yield metric
