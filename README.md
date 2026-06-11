# GCP Monitor Exporter

一个基于 Python 的 Prometheus Exporter，用于从 Google Cloud Monitoring API 采集 **Cloud SQL** 和 **Memorystore (Redis)** 的 CPU 使用率指标，并暴露给 Prometheus 进行监控和告警。

## 项目概述

GCP 内置的 Cloud SQL 和 Memorystore 监控指标无法直接被 Prometheus 抓取，本项目作为中间层，定期调用 GCP Cloud Monitoring API 查询指标数据，并转换为 Prometheus 格式对外暴露。

### 支持的指标

| 服务 | 指标名称 | Prometheus 指标名 | 说明 |
|------|----------|------------------|------|
| Cloud SQL | `cloudsql.googleapis.com/database/cpu/utilization` | `gcp_cloudsql_cpu_utilization` | CPU 使用率（0~1） |
| Memorystore | `redis.googleapis.com/stats/cpu_utilization` | `gcp_memorystore_cpu_utilization` | CPU 使用率（0~1） |

### 时间颗粒度说明

- **原始采样间隔**：GCP 内置指标每 **60秒** 采样一次
- **数据延迟**：指标数据通常有 **3~5分钟** 的延迟
- **API 查询最小颗粒度**：60秒（无法更细）
- **Exporter 默认采集间隔**：60秒
- **Prometheus 建议抓取间隔**：60~120秒

> 注：GCP 10秒高分辨率仅适用于自定义指标/Prometheus/Agent 指标，不适用于 Cloud SQL/Memorystore 内置指标。

## 项目结构

```
gcp_monitor/
├── README.md
├── requirements.txt                     # Python 依赖
├── config.yaml                          # 配置文件（GCP 项目、实例列表）
├── main.py                              # 程序入口
├── exporter/
│   ├── __init__.py
│   ├── gcp_client.py                    # GCP Cloud Monitoring API 客户端
│   ├── metrics.py                       # Prometheus 指标定义
│   └── collector.py                     # 指标采集逻辑
└── gcp-monitor-exporter.service         # systemd 服务单元文件
```

## 快速开始

### 1. 前置条件

- Python 3.9+
- GCP 项目已启用 Cloud Monitoring API
- GCP 服务账号拥有 `roles/monitoring.viewer` 权限
- 服务账号密钥文件（JSON 格式）

### 2. 安装

```bash
# 克隆项目
git clone <repo-url>
cd gcp_monitor

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置

#### 设置 GCP 认证

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
```

#### 编辑配置文件 `config.yaml`

```yaml
gcp:
  project_id: "your-gcp-project-id"

# 采集间隔（秒），默认 60，不建议低于 60
scrape_interval: 60

# 查询时间偏移（秒），应对 GCP 数据延迟，默认 240（4分钟）
query_offset: 240

# Prometheus 监听端口
port: 9100

# Cloud SQL 实例列表（留空则自动发现项目下所有实例）
cloudsql:
  enabled: true
  instances: []  # 例如: ["my-sql-instance-1", "my-sql-instance-2"]

# Memorystore (Redis) 实例列表（留空则自动发现）
memorystore:
  enabled: true
  instances: []  # 例如: ["my-redis-instance-1"]
```

### 4. 运行

```bash
python main.py
```

Exporter 默认在 `http://localhost:9168/metrics` 暴露指标。

### 5. 验证

```bash
curl http://localhost:9168/metrics
```

输出示例：

```text
# HELP gcp_cloudsql_cpu_utilization Cloud SQL CPU utilization (0-1)
# TYPE gcp_cloudsql_cpu_utilization gauge
gcp_cloudsql_cpu_utilization{instance="my-sql-instance",project="my-project"} 0.35

# HELP gcp_memorystore_cpu_utilization Memorystore Redis CPU utilization (0-1)
# TYPE gcp_memorystore_cpu_utilization gauge
gcp_memorystore_cpu_utilization{instance="my-redis-instance",project="my-project"} 0.12

# HELP gcp_monitor_scrape_duration_seconds Time taken to scrape metrics from GCP
# TYPE gcp_monitor_scrape_duration_seconds gauge
gcp_monitor_scrape_duration_seconds 1.23

# HELP gcp_monitor_scrape_success Whether the last scrape was successful (1=success, 0=failure)
# TYPE gcp_monitor_scrape_success gauge
gcp_monitor_scrape_success 1
```

## systemd 部署（推荐）

### 1. 部署文件

```bash
# 将项目文件复制到目标目录
sudo cp -r . /opt/gcp_monitor

# 创建专用运行用户（无需登录权限）
sudo useradd -r -s /usr/sbin/nologin gcp-exporter

# 设置服务账号密钥（确保文件权限最小化）
sudo cp /path/to/service-account-key.json /opt/gcp_monitor/service-account-key.json
sudo chmod 400 /opt/gcp_monitor/service-account-key.json
sudo chown gcp-exporter:gcp-exporter /opt/gcp_monitor/service-account-key.json

# 安装 Python 依赖（系统级或虚拟环境均可）
cd /opt/gcp_monitor
sudo pip3 install -r requirements.txt
```

### 2. 注册并启动服务

```bash
# 复制 service 文件到 systemd 目录
sudo cp /opt/gcp_monitor/gcp-monitor-exporter.service /etc/systemd/system/

# 重载 systemd 并启用开机自启
sudo systemctl daemon-reload
sudo systemctl enable gcp-monitor-exporter

# 启动服务
sudo systemctl start gcp-monitor-exporter

# 查看状态
sudo systemctl status gcp-monitor-exporter
```

### 3. 查看日志

```bash
# 实时日志
sudo journalctl -u gcp-monitor-exporter -f

# 最近 100 行日志
sudo journalctl -u gcp-monitor-exporter -n 100 --no-pager
```

### 4. 常用管理命令

```bash
sudo systemctl restart gcp-monitor-exporter   # 重启（配置修改后）
sudo systemctl stop    gcp-monitor-exporter   # 停止
sudo systemctl disable gcp-monitor-exporter   # 禁用开机自启
```

## 直接运行（开发调试）

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
python main.py --config config.yaml --log-level DEBUG
```

## Prometheus 配置

在 `prometheus.yml` 中添加抓取任务：

```yaml
scrape_configs:
  - job_name: 'gcp_monitor'
    scrape_interval: 60s
    scrape_timeout: 30s
    static_configs:
      - targets: ['<exporter-host>:9168']
```

## 配置参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `gcp.project_id` | string | - | GCP 项目 ID（必填） |
| `scrape_interval` | int | 60 | 采集间隔，单位秒，最小 60 |
| `query_offset` | int | 240 | 查询时间偏移，应对数据延迟 |
| `port` | int | 9168 | Prometheus 指标暴露端口 |
| `cloudsql.enabled` | bool | true | 是否采集 Cloud SQL 指标 |
| `cloudsql.instances` | list | [] | 指定实例，空则自动发现 |
| `memorystore.enabled` | bool | true | 是否采集 Memorystore 指标 |
| `memorystore.instances` | list | [] | 指定实例，空则自动发现 |

## 环境变量

| 变量名 | 说明 |
|--------|------|
| `GOOGLE_APPLICATION_CREDENTIALS` | GCP 服务账号密钥文件路径 |

## 扩展方向

- 支持更多 GCP 服务（Compute Engine、GKE、Pub/Sub 等）
- 支持更多指标类型（内存、磁盘、网络、连接数等）
- 支持多项目同时监控
- 添加 Grafana Dashboard 模板

## License

MIT
