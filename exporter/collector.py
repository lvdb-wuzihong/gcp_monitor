"""
指标采集逻辑模块 — CustomCollector 模式

每次 Prometheus 抓取时调用 collect()，实时查询 GCP API。
使用 ALIGN_MEAN 聚合后，每个实例只返回一条 TimeSeries（窗口内均值）。
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

    @staticmethod
    def _extract_cloudsql_instance_name(database_id: str) -> str:
        """
        从 database_id 提取实例名
        格式: "project_id:instance_name" → "instance_name"
        """
        if ":" in database_id:
            return database_id.split(":", 1)[1]
        return database_id

    @staticmethod
    def _extract_memorystore_instance_name(instance_id: str) -> str:
        """
        从完整路径提取实例名
        格式: "projects/xxx/locations/yyy/instances/zzz" → "zzz"
        """
        if "/" in instance_id:
            return instance_id.rsplit("/", 1)[-1]
        return instance_id

    def _collect_cloudsql(self):
        """采集 Cloud SQL CPU 使用率"""
        metric = GaugeMetricFamily(
            "gcp_cloudsql_cpu_utilization",
            "Cloud SQL CPU Utilization (percentage 0-100)",
            labels=["instance"],
        )

        instances = self.cloudsql_config.get("instances", []) or []

        results = self.gcp_client.query_cloudsql_cpu(
            start_offset=self.start_offset,
            end_offset=self.end_offset,
            instances=instances if instances else None,
        )

        count = 0
        for ts in results:
            raw_id = ts.resource.labels.get("database_id", "unknown")
            instance_name = self._extract_cloudsql_instance_name(raw_id)

            if not ts.points:
                continue

            latest_point = ts.points[0]
            cpu_val = latest_point.value.double_value * 100

            timestamp_ms = int(
                latest_point.interval.start_time.timestamp() * 1000
            )

            metric.add_metric(
                labels=[instance_name],
                value=round(cpu_val, 2),
                timestamp=timestamp_ms,
            )
            count += 1
            logger.info(f"Cloud SQL [{instance_name}] CPU: {cpu_val:.2f}%")

        logger.info(f"Cloud SQL: 输出 {count} 个实例指标")
        yield metric

    def _collect_memorystore(self):
        """
        采集 Memorystore (Redis) CPU 时间占比

        注意：redis.googleapis.com/stats/cpu_utilization 的单位是
        CPU 秒/秒（不是百分比），乘以 100 转为百分比形式。
        GCP 官方建议阈值：不要超过 0.8（即 80%）。
        """
        metric = GaugeMetricFamily(
            "gcp_memorystore_cpu_utilization",
            "Memorystore Redis CPU seconds per second * 100 (GCP threshold: 80 = 0.8s/s)",
            labels=["instance"],
        )

        instances = self.memorystore_config.get("instances", []) or []

        results = self.gcp_client.query_memorystore_cpu(
            start_offset=self.start_offset,
            end_offset=self.end_offset,
        )

        count = 0
        # 兑底合并：同一实例的多个 metric label 维度（cpu_type/process_type）
        # 按实例名累加，CPU 秒/秒是可加的
        merged = {}  # {instance_name: {"value": float, "timestamp_ms": int}}

        for ts in results:
            raw_id = ts.resource.labels.get("instance_id", "unknown")
            instance_name = self._extract_memorystore_instance_name(raw_id)

            # 如果配置了实例列表，只保留配置的实例
            if instances and instance_name not in instances:
                continue

            if not ts.points:
                continue

            latest_point = ts.points[0]
            cpu_val = latest_point.value.double_value  # 原始值 s/s
            timestamp_ms = int(
                latest_point.interval.start_time.timestamp() * 1000
            )

            # 调试日志：打印每条 TimeSeries 的 metric labels 和值
            metric_labels = dict(ts.metric.labels) if ts.metric.labels else {}
            logger.info(
                f"  [DEBUG] {instance_name} "
                f"labels={metric_labels} "
                f"value={cpu_val:.4f} s/s "
                f"points={len(ts.points)}"
            )

            if instance_name in merged:
                # 同一实例多条 → 累加（user/system/main/background 可加）
                merged[instance_name]["value"] += cpu_val
                # 取较新的时间戳
                merged[instance_name]["timestamp_ms"] = max(
                    merged[instance_name]["timestamp_ms"], timestamp_ms
                )
            else:
                merged[instance_name] = {"value": cpu_val, "timestamp_ms": timestamp_ms}

        for instance_name, data in merged.items():
            cpu_pct = data["value"] * 100
            metric.add_metric(
                labels=[instance_name],
                value=round(cpu_pct, 2),
                timestamp=data["timestamp_ms"],
            )
            count += 1
            logger.info(
                f"Memorystore [{instance_name}] CPU: {cpu_pct:.2f} "
                f"(raw: {data['value']:.4f} s/s)"
            )

        logger.info(f"Memorystore: 输出 {count} 个实例指标")
        yield metric
