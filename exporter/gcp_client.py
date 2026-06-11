"""
GCP Cloud Monitoring API 客户端
负责与 Google Cloud Monitoring API 交互，查询 Cloud SQL 和 Memorystore 的 CPU 使用率指标。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from google.cloud import monitoring_v3

logger = logging.getLogger(__name__)


class GCPMonitoringClient:
    """GCP Cloud Monitoring API 客户端封装"""

    # GCP 内置指标的最小采样间隔（秒）
    MIN_ALIGNMENT_PERIOD = 60

    # 指标类型定义
    CLOUDSQL_CPU_METRIC = "cloudsql.googleapis.com/database/cpu/utilization"
    MEMORystore_CPU_METRIC = "redis.googleapis.com/stats/cpu_utilization"

    # 资源类型定义
    CLOUDSQL_RESOURCE_TYPE = "cloudsql_database"
    MEMORystore_RESOURCE_TYPE = "redis_instance"

    def __init__(self, project_id: str):
        """
        初始化 GCP Monitoring 客户端

        Args:
            project_id: GCP 项目 ID
        """
        self.project_id = project_id
        self.project_name = f"projects/{project_id}"
        try:
            self.client = monitoring_v3.MetricServiceClient()
            logger.info(f"GCP Monitoring 客户端初始化成功，项目: {project_id}")
        except Exception as e:
            logger.error(f"GCP Monitoring 客户端初始化失败: {e}")
            raise

    def _build_time_interval(
        self, end_offset_seconds: int, start_offset_seconds: int
    ) -> monitoring_v3.TimeInterval:
        """
        构建查询的时间区间

        通过两个偏移量定义窗口：
          end_time   = now - end_offset_seconds   （窗口结束时间，距当前多久）
          start_time = now - start_offset_seconds  （窗口开始时间，距当前多久）

        例如：end_offset=60, start_offset=360
          → 查询 (now-6分钟) 到 (now-1分钟) 的数据

        Args:
            end_offset_seconds:   end_time 距当前的偏移（秒），较小值
            start_offset_seconds: start_time 距当前的偏移（秒），较大值

        Returns:
            TimeInterval 对象
        """
        now = datetime.now(timezone.utc)
        end_time = now - timedelta(seconds=end_offset_seconds)
        start_time = now - timedelta(seconds=start_offset_seconds)

        logger.debug(
            f"查询时间区间: start={start_time.isoformat()}, end={end_time.isoformat()}"
        )

        return monitoring_v3.TimeInterval(
            {
                "end_time": {"seconds": int(end_time.timestamp())},
                "start_time": {"seconds": int(start_time.timestamp())},
            }
        )

    def _build_instance_filter(
        self, metric_type: str, resource_type: str, label_key: str, instances: List[str]
    ) -> str:
        """
        构建指标查询过滤器

        Args:
            metric_type: GCP 指标类型
            resource_type: GCP 资源类型
            label_key: 实例 ID 对应的资源标签键名
            instances: 实例列表

        Returns:
            过滤器字符串
        """
        base_filter = (
            f'metric.type = "{metric_type}" '
            f'AND resource.type = "{resource_type}"'
        )

        if instances:
            instance_conditions = [
                f'resource.labels.{label_key} = "{inst}"' for inst in instances
            ]
            base_filter += " AND (" + " OR ".join(instance_conditions) + ")"

        return base_filter

    def query_cloudsql_cpu(
        self, offset_seconds: int, instances: List[str] = None
    ) -> List:
        """
        查询 Cloud SQL CPU 使用率

        指标: cloudsql.googleapis.com/database/cpu/utilization
        值类型: DOUBLE（0.0 ~ 1.0）
        资源标签: database_id（格式为 project_id:instance_name）

        时间窗口: (now - offset*3, now - offset/3)
        例: offset=180 → 查询 (now-540s, now-60s)，即 9 分钟前到 1 分钟前

        Args:
            offset_seconds: 查询时间偏移基准（秒），用于应对数据延迟
            instances: 指定实例列表，为空则查询所有实例

        Returns:
            TimeSeries 列表
        """
        try:
            filter_str = self._build_instance_filter(
                self.CLOUDSQL_CPU_METRIC,
                self.CLOUDSQL_RESOURCE_TYPE,
                "database_id",
                instances or [],
            )

            # 窗口结束时间：offset/3 秒前（留一定延迟余量）
            # 窗口开始时间：offset*3 秒前（覆盖足够宽的数据范围）
            end_offset = max(60, offset_seconds // 3)
            start_offset = offset_seconds * 3
            interval = self._build_time_interval(end_offset, start_offset)

            logger.info(
                f"Cloud SQL 查询 filter={filter_str}, "
                f"end_offset={end_offset}s, start_offset={start_offset}s"
            )

            request = monitoring_v3.ListTimeSeriesRequest(
                name=self.project_name,
                filter=filter_str,
                interval=interval,
                aggregation=monitoring_v3.Aggregation(
                    alignment_period={"seconds": self.MIN_ALIGNMENT_PERIOD},
                    per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_MEAN,
                ),
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            )

            results = list(self.client.list_time_series(request=request))
            logger.info(f"Cloud SQL CPU 查询完成，获取到 {len(results)} 个时间序列")
            return results

        except Exception as e:
            logger.error(f"查询 Cloud SQL CPU 使用率失败: {e}", exc_info=True)
            return []

    def query_memorystore_cpu(
        self, offset_seconds: int, instances: List[str] = None
    ) -> List:
        """
        查询 Memorystore (Redis) CPU 使用率

        指标: redis.googleapis.com/stats/cpu_utilization
        值类型: DOUBLE（0.0 ~ 1.0）
        资源标签: instance_id

        Args:
            offset_seconds: 查询时间偏移基准（秒），用于应对数据延迟
            instances: 指定实例列表，为空则查询所有实例

        Returns:
            TimeSeries 列表
        """
        try:
            filter_str = self._build_instance_filter(
                self.MEMORystore_CPU_METRIC,
                self.MEMORystore_RESOURCE_TYPE,
                "instance_id",
                instances or [],
            )

            end_offset = max(60, offset_seconds // 3)
            start_offset = offset_seconds * 3
            interval = self._build_time_interval(end_offset, start_offset)

            logger.info(
                f"Memorystore 查询 filter={filter_str}, "
                f"end_offset={end_offset}s, start_offset={start_offset}s"
            )

            request = monitoring_v3.ListTimeSeriesRequest(
                name=self.project_name,
                filter=filter_str,
                interval=interval,
                aggregation=monitoring_v3.Aggregation(
                    alignment_period={"seconds": self.MIN_ALIGNMENT_PERIOD},
                    per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_MEAN,
                ),
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            )

            results = list(self.client.list_time_series(request=request))
            logger.info(f"Memorystore CPU 查询完成，获取到 {len(results)} 个时间序列")
            return results

        except Exception as e:
            logger.error(f"查询 Memorystore CPU 使用率失败: {e}", exc_info=True)
            return []

    def discover_cloudsql_instances(self) -> List[str]:
        """
        自动发现 Cloud SQL 实例

        通过查询最近 15 分钟的 CPU 指标数据，从返回的时间序列中提取实例名称。
        窗口: (now-15min, now-60s)，覆盖延迟同时尽量获取最新数据。

        Returns:
            实例名称列表
        """
        try:
            filter_str = self._build_instance_filter(
                self.CLOUDSQL_CPU_METRIC, self.CLOUDSQL_RESOURCE_TYPE, "database_id", []
            )

            # 查询最近 15 分钟，end_time 距当前 60 秒（留最小延迟余量）
            interval = self._build_time_interval(
                end_offset_seconds=60, start_offset_seconds=900
            )

            logger.info(f"Cloud SQL 自动发现 filter={filter_str}")

            request = monitoring_v3.ListTimeSeriesRequest(
                name=self.project_name,
                filter=filter_str,
                interval=interval,
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.HEADERS,
            )

            results = self.client.list_time_series(request=request)
            instances = []
            for ts in results:
                labels = ts.resource.labels
                logger.debug(f"Cloud SQL 资源标签: {dict(labels)}")
                # database_id 格式通常为 "project_id:instance_name"
                db_id = labels.get("database_id", "")
                if ":" in db_id:
                    instance_name = db_id.split(":", 1)[1]
                else:
                    instance_name = db_id
                if instance_name and instance_name not in instances:
                    instances.append(instance_name)

            logger.info(f"自动发现 Cloud SQL 实例 ({len(instances)} 个): {instances}")
            return instances

        except Exception as e:
            logger.error(f"自动发现 Cloud SQL 实例失败: {e}", exc_info=True)
            return []

    def discover_memorystore_instances(self) -> List[str]:
        """
        自动发现 Memorystore (Redis) 实例

        通过查询最近 15 分钟的 CPU 指标数据，从返回的时间序列中提取实例名称。
        窗口: (now-15min, now-60s)。

        Returns:
            实例名称列表
        """
        try:
            filter_str = self._build_instance_filter(
                self.MEMORystore_CPU_METRIC,
                self.MEMORystore_RESOURCE_TYPE,
                "instance_id",
                [],
            )

            interval = self._build_time_interval(
                end_offset_seconds=60, start_offset_seconds=900
            )

            logger.info(f"Memorystore 自动发现 filter={filter_str}")

            request = monitoring_v3.ListTimeSeriesRequest(
                name=self.project_name,
                filter=filter_str,
                interval=interval,
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.HEADERS,
            )

            results = self.client.list_time_series(request=request)
            instances = []
            for ts in results:
                labels = ts.resource.labels
                logger.debug(f"Memorystore 资源标签: {dict(labels)}")
                instance_id = labels.get("instance_id", "")
                if instance_id and instance_id not in instances:
                    instances.append(instance_id)

            logger.info(f"自动发现 Memorystore 实例 ({len(instances)} 个): {instances}")
            return instances

        except Exception as e:
            logger.error(f"自动发现 Memorystore 实例失败: {e}", exc_info=True)
            return []
