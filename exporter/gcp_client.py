"""
GCP Cloud Monitoring API 客户端

查询策略：
1. filter 只用 metric.type，不加 resource.type 限制
2. 使用 ALIGN_MEAN 聚合：将窗口内多个数据点合并为一个均值，每个实例只返回一条 TimeSeries
3. 时间窗口用 (now - start_offset, now - end_offset) 覆盖延迟
"""

import logging
import time
from typing import List

from google.cloud import monitoring_v3

logger = logging.getLogger(__name__)


class GCPMonitoringClient:
    """GCP Cloud Monitoring API 客户端封装"""

    # 指标类型
    CLOUDSQL_CPU_METRIC = "cloudsql.googleapis.com/database/cpu/utilization"
    MEMORystore_CPU_METRIC = "redis.googleapis.com/stats/cpu_utilization_main_thread"

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.project_name = f"projects/{project_id}"
        self.client = monitoring_v3.MetricServiceClient()
        logger.info(f"GCP Monitoring 客户端初始化成功，项目: {project_id}")

    def _build_interval(self, start_offset: int, end_offset: int):
        """
        构建查询时间区间

        Args:
            start_offset: start_time 距当前的秒数（较大值，如 600 = 10分钟前）
            end_offset:   end_time 距当前的秒数（较小值，如 180 = 3分钟前）

        Returns:
            TimeInterval
        """
        now = time.time()
        return monitoring_v3.TimeInterval({
            "end_time": {"seconds": int(now - end_offset)},
            "start_time": {"seconds": int(now - start_offset)},
        })

    def _build_request(
        self, filter_str, start_offset, end_offset,
        group_by_fields=None
    ):
        """
        构建 ListTimeSeries 请求

        ALIGN_MEAN: 将窗口内多个数据点合并为一个均值
        REDUCE_MEAN + group_by_fields: 将同一实例的多个 shard/node 合并为一个均值
        """
        interval = self._build_interval(start_offset, end_offset)
        alignment_period = start_offset - end_offset

        aggregation = {
            "alignment_period": {"seconds": alignment_period},
            "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_MEAN,
        }

        # cross_series_reducer: 将同一实例的多个子序列（shard/node）聚合为一个
        if group_by_fields:
            aggregation["cross_series_reducer"] = (
                monitoring_v3.Aggregation.Reducer.REDUCE_MEAN
            )
            aggregation["group_by_fields"] = group_by_fields

        return {
            "name": self.project_name,
            "filter": filter_str,
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            "aggregation": aggregation,
        }

    @staticmethod
    def _add_instance_filter(base_filter: str, label_key: str, instances: List[str]) -> str:
        """
        在 base filter 上追加实例过滤条件

        Args:
            base_filter: 基础过滤器（如 metric.type = "..."）
            label_key:   资源标签键名（如 database_id、instance_id）
            instances:   实例名列表

        Returns:
            追加了实例条件的过滤器字符串
        """
        if not instances:
            return base_filter

        conditions = [
            f'resource.labels.{label_key} = "{inst}"' for inst in instances
        ]
        return base_filter + " AND (" + " OR ".join(conditions) + ")"

    def query_cloudsql_cpu(
        self, start_offset: int = 600, end_offset: int = 180,
        instances: List[str] = None
    ) -> List:
        """
        查询 Cloud SQL CPU 使用率（窗口内均值）

        Args:
            start_offset: 查询窗口起始（距当前秒数）
            end_offset:   查询窗口结束（距当前秒数）
            instances:    指定实例列表，为空则查询所有

        Returns:
            TimeSeries 列表（每个实例一条）
        """
        base_filter = f'metric.type = "{self.CLOUDSQL_CPU_METRIC}"'
        # database_id 完整格式为 "project_id:instance_name"，用精确匹配
        if instances:
            conditions = [
                f'resource.labels.database_id = "{self.project_id}:{inst}"'
                for inst in instances
            ]
            filter_str = base_filter + " AND (" + " OR ".join(conditions) + ")"
        else:
            filter_str = base_filter

        logger.info(f"Cloud SQL 查询: filter={filter_str}")

        try:
            request = self._build_request(filter_str, start_offset, end_offset)
            results = list(self.client.list_time_series(request=request))
            logger.info(f"Cloud SQL 返回 {len(results)} 个时间序列")
            return results

        except Exception as e:
            logger.error(f"查询 Cloud SQL CPU 失败: {e}", exc_info=True)
            return []

    def query_memorystore_cpu(
        self, start_offset: int = 600, end_offset: int = 180
    ) -> List:
        """
        查询 Memorystore (Redis) CPU 使用率（窗口内均值）

        仅用 ALIGN_MEAN 聚合时间维度。不同 metric label（cpu_type/process_type）
        仍会返回多条 TimeSeries，由 collector 层按实例名累加合并。

        Args:
            start_offset: 查询窗口起始（距当前秒数）
            end_offset:   查询窗口结束（距当前秒数）

        Returns:
            TimeSeries 列表（同一实例可能多条，collector 层合并）
        """
        filter_str = f'metric.type = "{self.MEMORystore_CPU_METRIC}"'

        logger.info(f"Memorystore 查询: filter={filter_str}")

        try:
            request = self._build_request(filter_str, start_offset, end_offset)
            results = list(self.client.list_time_series(request=request))
            logger.info(f"Memorystore 返回 {len(results)} 个时间序列")
            return results

        except Exception as e:
            logger.error(f"查询 Memorystore CPU 失败: {e}", exc_info=True)
            return []
