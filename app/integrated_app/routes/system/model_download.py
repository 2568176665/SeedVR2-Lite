#!/usr/bin/env python3
"""模型权重下载路由模块。

把「手工把权重放进 model/ 目录」变成系统状态页上的一个表单，
支持官方源与镜像源切换，并暴露进度供前端轮询。

API 端点：
- GET /api/system/model/download/options: 下载可选项（模型/精度/下载源 + 本地已存在状态）
- GET /api/system/model/download/status: 当前下载任务进度
- POST /api/system/model/download: 启动下载任务
- POST /api/system/model/download/cancel: 取消下载任务

进度实时推送：下载线程向 SSE 事件总线发布 ``model_download`` 事件，
前端经 /api/sse/events 实时接收（节流 5 次/秒），并保留轮询作为兼容兼底。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.integrated_app.dependencies import get_config
from app.integrated_app.services.model_download import (
    DOWNLOAD_SOURCES,
    PRECISIONS,
    model_download_manager,
)
from app.integrated_app.utils.response import respond_error, respond_success
from app.integrated_app.utils.weight_names import find_weight_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["模型下载"])


class ModelDownloadRequest(BaseModel):
    """下载请求体。"""

    size: str = "3b"
    precision: str = "fp8"
    source: str = "huggingface"


@router.get("/model/download/options")
async def download_options(config: dict = Depends(get_config)):
    """获取下载表单可选项。

    返回可用下载源、各模型支持的精度，以及每个精度在本地是否已存在权重，
    供前端置灰/标注「已下载」。

    Returns:
        JSON 响应，data 结构：
        {
          "sources": [{"id", "endpoint"}],
          "precisions": [str],
          "models": [{"id", "name", "precisions": [{"id", "present"}]}],
          "target_dir": str
        }
    """
    from app.integrated_app.config_models import get_pretrained_root

    model_cfg = config.get("model", {})
    root = get_pretrained_root(model_cfg)
    models = []
    for size, entry in (model_cfg.get("models") or {}).items():
        precisions = []
        for precision in PRECISIONS:
            if not entry.get(f"checkpoint_{precision}"):
                continue
            precisions.append({"id": precision, "present": _weights_present(root, entry, precision)})
        models.append({"id": size, "name": entry.get("name", size), "precisions": precisions})

    return respond_success(
        data={
            "sources": [{"id": sid, "endpoint": url} for sid, url in DOWNLOAD_SOURCES.items()],
            "precisions": list(PRECISIONS),
            "models": models,
            "target_dir": root,
        }
    )


def _weights_present(root: str, entry: dict, precision: str) -> bool:
    """判断某精度的权重是否已在盘（DiT 与 VAE 均存在才算可用，按双命名别名探测）。"""
    if find_weight_file(root, entry[f"checkpoint_{precision}"]) is None:
        return False
    vae = entry.get("vae_checkpoint")
    return not vae or find_weight_file(root, vae) is not None


@router.get("/model/download/status")
async def download_status():
    """获取当前下载任务进度。

    Returns:
        JSON 响应，data 为任务状态快照（state: idle/running/done/error）。
    """
    return respond_success(data=model_download_manager.get_status())


@router.post("/model/download/cancel")
async def cancel_download():
    """取消当前模型下载任务。

    取消在下一个分块边界生效；已下载的 ``.part`` 分片会保留，
    再次下载同一精度时可用 Range 续传。

    Returns:
        JSON 响应，data 为请求取消后的任务状态；无运行中任务时返回 409。
    """
    try:
        status = model_download_manager.cancel()
    except RuntimeError as e:
        return respond_error("NO_ACTIVE_DOWNLOAD", str(e), status=409)
    return respond_success(data=status)


@router.post("/model/download")
async def start_download(req: ModelDownloadRequest, config: dict = Depends(get_config)):
    """启动模型权重下载任务。

    请求体（JSON）：
    {
        "size": str,       // 模型大小，如 "3b" / "7b" / "7b_sharp"
        "precision": str,  // 精度，如 "fp8"
        "source": str      // 下载源 id，见 GET /options
    }

    Returns:
        JSON 响应，data 为初始任务状态；参数非法返回 400，任务冲突返回 409。
    """
    try:
        status = model_download_manager.start(config.get("model", {}), req.size, req.precision, req.source)
    except ValueError as e:
        return respond_error("INVALID_ARGUMENT", str(e), status=400)
    except RuntimeError as e:
        return respond_error("DOWNLOAD_IN_PROGRESS", str(e), status=409)
    except Exception as e:  # noqa: BLE001 — 端点兜底，避免异常细节泄漏到响应
        logger.error(f"启动模型下载失败: {e}")
        return respond_error("DOWNLOAD_START_FAILED", "启动下载任务失败", status=500)
    return respond_success(data=status)
