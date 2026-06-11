"""
Prometheus 指标定义模块
集中定义所有对外暴露的 Prometheus 指标（Gauge / Counter / Summary 等）。
"""

from prometheus_client import Gauge


# =============================================================================
# Cloud SQL 指标
# =============================================================================

CLOUDSQL_CPU_UTILIZATION = Gauge(
    "gcp_cloudsql_cpu_utilization",
    "Cloud SQL 实例 CPU 使用率（0.0 ~ 1.0，即 0% ~ 100%）",
    ["project", "instance", "region"],
)

# =============================================================================
# Memorystore (Redis) 指标
# =============================================================================

MEMORystore_CPU_UTILIZATION = Gauge(
    "gcp_memorystore_cpu_utilization",
    "Memorystore (Redis) 实例 CPU 使用率（0.0 ~ 1.0，即 0% ~ 100%）",
    ["project", "instance", "region"],
)

# =============================================================================
# Exporter 自身运行指标
# =============================================================================

SCRAPE_DURATION_SECONDS = Gauge(
    "gcp_monitor_scrape_duration_seconds",
    "上一次从 GCP 采集指标所消耗的时间（秒）",
)

SCRAPE_SUCCESS = Gauge(
    "gcp_monitor_scrape_success",
    "上一次指标采集是否成功（1=成功，0=失败）",
)

SCRAPE_TIMESTAMP = Gauge(
    "gcp_monitor_last_scrape_timestamp_seconds",
    "上一次指标采集完成时的 Unix 时间戳",
)

CLOUDSQL_INSTANCE_COUNT = Gauge(
    "gcp_monitor_cloudsql_instance_count",
    "当前正在监控的 Cloud SQL 实例数量",
)

MEMORystore_INSTANCE_COUNT = Gauge(
    "gcp_monitor_memorystore_instance_count",
    "当前正在监控的 Memorystore 实例数量",
)
