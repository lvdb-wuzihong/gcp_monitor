"""
GCP Cloud Monitoring API 客户端

核心原则（参照 Google 官方示例）：
1. filter 只用 metric.type，不加 resource.type 限制，避免类型名不匹配导致空结果
2. 不指定 aggregation，让 API 返回原始数据点
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
    MEMORystore_CPU_METRIC = "redis.googleapis.com/stats/cpu_utilization"

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

    def query_cloudsql_cpu(
        self, start_offset: int = 600, end_offset: int = 180
    ) -> List:
        """
        查询 Cloud SQL CPU 使用率

        filter 只指定 metric.type，不限制 resource.type。
        不使用 aggregation，获取原始数据点。

        Args:
            start_offset: 查询窗口起始（距当前秒数），默认 600（10分钟前）
            end_offset:   查询窗口结束（距当前秒数），默认 180（3分钟前）

        Returns:
            TimeSeries 列表
        """
        filter_str = f'metric.type = "{self.CLOUDSQL_CPU_METRIC}"'
        interval = self._build_interval(start_offset, end_offset)

        logger.info(
            f"Cloud SQL 查询: filter={filter_str}, "
            f"window=(now-{start_offset}s, now-{end_offset}s)"
        )

        try:
            results = self.client.list_time_series(request={
                "name": self.project_name,
                "filter": filter_str,
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            })
            results = list(results)
            logger.info(f"Cloud SQL 返回 {len(results)} 个时间序列")

            # 打印每个时间序列的资源标签，帮助确认 label 格式
            for ts in results:
                logger.debug(
                    f"  resource.labels={dict(ts.resource.labels)}, "
                    f"points_count={len(ts.points)}"
                )
            return results

        except Exception as e:
            logger.error(f"查询 Cloud SQL CPU 失败: {e}", exc_info=True)
            return []

    def query_memorystore_cpu(
        self, start_offset: int = 600, end_offset: int = 180
    ) -> List:
        """
        查询 Memorystore (Redis) CPU 使用率

        Args:
            start_offset: 查询窗口起始（距当前秒数），默认 600
            end_offset:   查询窗口结束（距当前秒数），默认 180

        Returns:
            TimeSeries 列表
        """
        filter_str = f'metric.type = "{self.MEMORystore_CPU_METRIC}"'
        interval = self._build_interval(start_offset, end_offset)

        logger.info(
            f"Memorystore 查询: filter={filter_str}, "
            f"window=(now-{start_offset}s, now-{end_offset}s)"
        )

        try:
            results = self.client.list_time_series(request={
                "name": self.project_name,
                "filter": filter_str,
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            })
            results = list(results)
            logger.info(f"Memorystore 返回 {len(results)} 个时间序列")

            for ts in results:
                logger.debug(
                    f"  resource.labels={dict(ts.resource.labels)}, "
                    f"points_count={len(ts.points)}"
                )
            return results

        except Exception as e:
            logger.error(f"查询 Memorystore CPU 失败: {e}", exc_info=True)
            return []
