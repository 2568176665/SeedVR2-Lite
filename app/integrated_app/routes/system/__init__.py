#!/usr/bin/env python3
"""系统路由包。

本包包含 SeedVR2 项目的系统管理相关路由模块，按职责拆分如下：
- health.py: 系统健康检查与存活探针
- readiness.py: 容器编排就绪探针（模型预热中返回 503）
- gpu.py: GPU 信息查询端点
- settings.py: 系统设置管理与模型操作端点
- model_download.py: 模型权重下载（选模型/精度/下载源，支持镜像源）
- history.py: 历史记录查询与管理端点
- sse.py: 全局 SSE 事件总线端点

API 路由前缀：
- /api/system: 系统相关 API（health/gpu/settings/history）
- /api/sse/events: SSE 事件流端点（无额外前缀）

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""
