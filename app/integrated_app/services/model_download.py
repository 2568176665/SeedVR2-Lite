#!/usr/bin/env python3
"""SeedVR2 - 模型权重下载服务。

在系统状态页提供「选模型 + 选精度 + 选下载源」的权重下载能力，
把此前只存在于分发说明里的「手工下载到 model/ 目录」变成可点的表单。

权重来源（与 config.yaml 登记的 SHA256 一一对应，已实测核对）：
- DiT FP16 / FP8      → HuggingFace ``numz/SeedVR2_comfyUI``（根目录）
- DiT INT8/MXFP8/NVFP4 → HuggingFace ``Comfy-Org/SeedVR2``（``diffusion_models/``）
- VAE                 → HuggingFace ``numz/SeedVR2_comfyUI``
- pos_emb / neg_emb   → HuggingFace ``ByteDance-Seed/SeedVR2-3B``
  （7B 系复用 3B 仓库的文本嵌入，config.yaml 三者哈希相同）

实现要点：
- 单任务互斥：同一时刻只允许一个下载任务，避免多线程抢同一目标文件。
- 断点续传：先写 ``<目标文件>.part``，用 HTTP Range 续传；完成后才改名为正式文件，
  半成品不会被加载器误认为可用权重。
- 完整性校验：下载完成后按 config.yaml 的 SHA256 候选校验，不匹配则删除文件并报错
  （加载期是 fail-closed 的，放过坏文件只会让用户在推理时才撞墙）。
- 不做离线限制规避：本模块**不使用** huggingface_hub（引擎模块已把
  ``HF_HUB_OFFLINE`` 置 1，其常量在 import 时固化），直接走 HTTP 下载，
  因此下载源可以在请求时自由切换官方站 / 镜像站。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.integrated_app.config_models import get_pretrained_root
from app.integrated_app.security.integrity_check import compute_sha256
from app.integrated_app.utils.weight_names import weight_hash_candidates

logger = logging.getLogger(__name__)

# 可选的下载源（前端下拉直接消费）。键为 source id，值为可替换的 endpoint。
DOWNLOAD_SOURCES: dict[str, str] = {
    "huggingface": "https://huggingface.co",
    "hf-mirror": "https://hf-mirror.com",
}

# 支持下载的精度（顺序与 config.yaml 的 checkpoint_<precision> 键一致）
PRECISIONS: tuple[str, ...] = ("fp16", "fp8", "mxfp8", "int8_convrot", "nvfp4")

# FP16/FP8 走 numz 仓（config.yaml 主哈希即该仓字节）；量化格式只在 Comfy-Org 仓
_NUMZ_REPO = "numz/SeedVR2_comfyUI"
_COMFY_ORG_REPO = "Comfy-Org/SeedVR2"
_EMBED_REPO = "ByteDance-Seed/SeedVR2-3B"
_QUANTIZED = frozenset({"mxfp8", "int8_convrot", "nvfp4"})

_USER_AGENT = "SeedVR2-Portable/1.6"
_CHUNK_SIZE = 1024 * 1024
_TIMEOUT = 60.0


@dataclass
class _FilePlan:
    """单个待下载文件的计划。"""

    filename: str
    url: str
    sha256_candidates: list[str] = field(default_factory=list)


def _repo_path(repo_id: str, repo_file: str) -> str:
    """拼出 HuggingFace ``resolve/main`` 的相对路径。"""
    return f"{repo_id}/resolve/main/{repo_file}"


def build_file_plan(model_config: dict, model_size: str, precision: str) -> list[_FilePlan]:
    """按模型大小与精度生成待下载文件清单。

    Args:
        model_config: config.yaml 的 ``model`` 配置节。
        model_size: 模型大小标识，如 "3b" / "7b" / "7b_sharp"。
        precision: 精度标识，见 :data:`PRECISIONS`。

    Returns:
        list[_FilePlan]: 依次为 DiT 权重、VAE、pos_emb、neg_emb 的下载计划。

    Raises:
        ValueError: 模型大小或精度不在 config.yaml 登记范围内。
    """
    models = model_config.get("models", {})
    model_cfg = models.get(model_size)
    if not model_cfg:
        raise ValueError(f"未知的模型大小: {model_size}（可选: {', '.join(models)}）")
    if precision not in PRECISIONS:
        raise ValueError(f"未知的精度: {precision}（可选: {', '.join(PRECISIONS)}）")

    checkpoint = model_cfg.get(f"checkpoint_{precision}")
    if not checkpoint:
        raise ValueError(f"模型 {model_size} 未登记 {precision} 精度的权重文件")

    plan: list[_FilePlan] = []

    if precision in _QUANTIZED:
        repo_file = f"diffusion_models/{Path(checkpoint).name}"
        dit_url = _repo_path(_COMFY_ORG_REPO, repo_file)
    else:
        dit_url = _repo_path(_NUMZ_REPO, Path(checkpoint).name)
    plan.append(_FilePlan(checkpoint, dit_url, weight_hash_candidates(model_cfg, precision)))

    vae = model_cfg.get("vae_checkpoint")
    if vae:
        plan.append(_FilePlan(vae, _repo_path(_NUMZ_REPO, Path(vae).name), weight_hash_candidates(model_cfg, "vae")))

    for key in ("pos_emb", "neg_emb"):
        name = model_cfg.get(key)
        if name:
            plan.append(
                _FilePlan(name, _repo_path(_EMBED_REPO, Path(name).name), weight_hash_candidates(model_cfg, key))
            )

    return plan


def _head_size(url: str) -> int:
    """HEAD 请求取远端文件大小（跟随重定向）；失败返回 0。"""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 - URL 由本模块常量拼装
            return int(resp.headers.get("Content-Length") or 0)
    except Exception as e:  # noqa: BLE001 — 大小仅用于进度显示，缺失不应中断下载
        logger.debug(f"HEAD 获取文件大小失败 {url}: {e}")
        return 0


class _CancelledError(Exception):
    """用户主动取消下载的内部信号（不视为错误）。"""


class ModelDownloadManager:
    """模型权重下载任务管理器（进程内单例，单任务互斥）。"""

    # 进度推送节流间隔（秒）：1MB 分块在 ~5MB/s 下约 5 次/秒，
    # 再叠加 SSE 序列化与前端重绘就成了纯浪费；状态变更点总是立即推送。
    _EMIT_INTERVAL = 0.2

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {"state": "idle"}
        self._cancel_event = threading.Event()
        self._last_emit = 0.0

    # ==================== 对外接口 ====================

    def get_status(self) -> dict[str, Any]:
        """返回当前下载任务状态快照。"""
        with self._lock:
            return dict(self._status)

    def is_running(self) -> bool:
        """是否有下载任务正在运行。"""
        with self._lock:
            return self._status.get("state") == "running"

    def cancel(self) -> dict[str, Any]:
        """请求取消当前下载任务。

        仅置位取消信号，实际退出由下载循环在下一个分块边界完成（避免撕裂文件）。
        已下载的 ``.part`` 分片刻意保留，下次下载同一精度时可 Range 续传。

        Returns:
            dict: 请求取消后的状态快照。

        Raises:
            RuntimeError: 当前没有运行中的下载任务。
        """
        with self._lock:
            if self._status.get("state") != "running":
                raise RuntimeError("当前没有进行中的下载任务")
            self._cancel_event.set()
            self._status["message"] = "正在取消..."
            return dict(self._status)

    def start(self, model_config: dict, model_size: str, precision: str, source: str) -> dict[str, Any]:
        """启动下载任务（后台线程执行）。

        Args:
            model_config: config.yaml 的 ``model`` 配置节。
            model_size: 模型大小标识。
            precision: 精度标识。
            source: 下载源 id，见 :data:`DOWNLOAD_SOURCES`。

        Returns:
            dict: 刚初始化的任务状态快照。

        Raises:
            ValueError: 参数非法（未知模型/精度/下载源）。
            RuntimeError: 已有下载任务在运行。
        """
        if source not in DOWNLOAD_SOURCES:
            raise ValueError(f"未知的下载源: {source}（可选: {', '.join(DOWNLOAD_SOURCES)}）")

        plan = build_file_plan(model_config, model_size, precision)
        target_dir = Path(get_pretrained_root(model_config))
        endpoint = DOWNLOAD_SOURCES[source]

        with self._lock:
            if self._status.get("state") == "running":
                raise RuntimeError("已有模型下载任务进行中，请等待其完成")

            self._cancel_event.clear()
            self._last_emit = 0.0
            self._status = {
                "state": "running",
                "model_size": model_size,
                "precision": precision,
                "source": source,
                "target_dir": str(target_dir),
                "current_file": "",
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "percent": 0.0,
                "message": "准备中...",
                "error": None,
                "files": [{"filename": p.filename, "status": "pending", "size": 0, "downloaded": 0} for p in plan],
                "started_at": time.time(),
                "finished_at": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(plan, endpoint, target_dir),
                name="model-download",
                daemon=True,
            )
            self._thread.start()
            return dict(self._status)

    # ==================== 内部实现 ====================

    def _update(self, **fields: Any) -> None:
        """线程安全地更新状态字段。"""
        with self._lock:
            self._status.update(fields)

    def _emit(self, force: bool = False) -> None:
        """把当前状态推送到 SSE 事件总线（节流）。

        下载线程无事件循环，故直接调用 ``event_bus.publish``（它内部用
        ``put_nowait``，明确支持跨线程调用）。不推送会退化为 1s 轮询，
        进度条只能一跳一跳地走。

        Args:
            force: True 时跳过节流（用于状态变更点，如开始/取消/完成）。
        """
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_emit < self._EMIT_INTERVAL:
                return
            self._last_emit = now
            snapshot = dict(self._status)
        try:
            from app.integrated_app.routes.system.sse import event_bus

            event_bus.publish("model_download", snapshot)
        except Exception as e:  # noqa: BLE001 — 推送失败不应影响下载本身
            logger.debug(f"下载进度推送失败: {e}")

    def _update_file(self, index: int, **fields: Any) -> None:
        """线程安全地更新某个文件条目的字段。"""
        with self._lock:
            files = self._status.get("files") or []
            if 0 <= index < len(files):
                files[index].update(fields)

    def _add_downloaded(self, index: int, nbytes: int) -> None:
        """累加已下载字节并刷新总进度。"""
        with self._lock:
            self._status["downloaded_bytes"] = self._status.get("downloaded_bytes", 0) + nbytes
            files = self._status.get("files") or []
            if 0 <= index < len(files):
                files[index]["downloaded"] = files[index].get("downloaded", 0) + nbytes
            total = self._status.get("total_bytes") or 0
            if total > 0:
                self._status["percent"] = round(min(100.0, self._status["downloaded_bytes"] * 100.0 / total), 1)
        self._emit()

    def _check_cancelled(self) -> None:
        """若已请求取消则抛出 _CancelledError。"""
        if self._cancel_event.is_set():
            raise _CancelledError

    def _run(self, plan: list[_FilePlan], endpoint: str, target_dir: Path) -> None:
        """后台线程主流程：探测大小 → 逐个下载 → 校验哈希。"""
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            self._update(message="探测文件大小...")
            self._emit(force=True)
            sizes = [_head_size(endpoint + "/" + p.url) for p in plan]
            with self._lock:
                for i, size in enumerate(sizes):
                    files = self._status["files"]
                    files[i]["size"] = size
                self._status["total_bytes"] = sum(sizes)
            self._emit(force=True)

            for index, item in enumerate(plan):
                self._check_cancelled()

                def _on_chunk(n: int, _i: int = index) -> None:
                    self._add_downloaded(_i, n)

                self._update(current_file=item.filename, message=f"下载 {item.filename}")
                self._update_file(index, status="downloading")
                self._emit(force=True)
                dest = target_dir / item.filename

                if self._existing_is_valid(dest, item):
                    self._update_file(index, status="skipped", downloaded=sizes[index])
                    self._add_downloaded(index, sizes[index])
                    continue

                url = endpoint + "/" + item.url
                try:
                    self._download_one(url, dest.with_suffix(dest.suffix + ".part"), _on_chunk)
                except _CancelledError:
                    self._update_file(index, status="cancelled")
                    raise
                except Exception as e:  # noqa: BLE001 — 任一文件失败即整体失败
                    self._update_file(index, status="failed")
                    raise RuntimeError(f"下载 {item.filename} 失败: {e}") from e

                self._update_file(index, status="verifying")
                self._update(current_file=item.filename, message=f"校验 {item.filename}")
                self._emit(force=True)
                part = dest.with_suffix(dest.suffix + ".part")
                if not self._verify(part, item):
                    part.unlink(missing_ok=True)
                    self._update_file(index, status="failed")
                    raise RuntimeError(f"{item.filename} 完整性校验失败（SHA256 与 config.yaml 不符），已删除该文件")

                part.replace(dest)
                self._update_file(index, status="done")
                self._emit(force=True)

            self._update(
                state="done",
                percent=100.0,
                current_file="",
                message="下载完成",
                finished_at=time.time(),
            )
            self._emit(force=True)
            logger.info(f"模型下载完成: {target_dir}")
        except _CancelledError:
            # 取消不是错误：保留 .part 分片以便续传，只把状态收尾。
            # 已完成的文件已是正式文件名，不会被回退。
            logger.info("模型下载已被用户取消")
            with self._lock:
                for f in self._status.get("files") or []:
                    if f.get("status") in ("pending", "downloading", "verifying"):
                        f["status"] = "cancelled"
                self._status.update(
                    state="cancelled",
                    current_file="",
                    message="下载已取消",
                    finished_at=time.time(),
                )
            self._emit(force=True)
        except Exception as e:  # noqa: BLE001 — 统一收敛为任务状态
            logger.error(f"模型下载任务失败: {e}")
            self._update(state="error", error=str(e), message=str(e), finished_at=time.time())
            self._emit(force=True)

    @staticmethod
    def _existing_is_valid(dest: Path, item: _FilePlan) -> bool:
        """已存在的正式文件是否可直接复用（哈希匹配）。

        未登记哈希时退化为「存在即复用」，与加载期的 skip_if_empty 语义一致。
        """
        if not dest.exists():
            return False
        if not item.sha256_candidates:
            return True
        try:
            return compute_sha256(dest).lower() in item.sha256_candidates
        except OSError:
            return False

    @staticmethod
    def _verify(path: Path, item: _FilePlan) -> bool:
        """校验下载完成（尚未改名）的文件哈希。"""
        if not path.exists():
            return False
        if not item.sha256_candidates:
            return True
        try:
            return compute_sha256(path).lower() in item.sha256_candidates
        except OSError:
            return False

    def _download_one(self, url: str, part: Path, on_chunk) -> None:
        """下载单个文件到 ``.part``，已存在分片时用 Range 续传。

        每写完一个分块检查一次取消信号：取消在分块边界生效，
        不会留下写了一半的分块；已写入的 ``.part`` 保留供下次续传。
        """
        resume_from = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": _USER_AGENT}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

        req = urllib.request.Request(url, headers=headers)  # noqa: S310 - URL 由本模块常量拼装
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            # 服务端不支持续传时会返回 200（整个文件），此时必须从头覆盖写
            resuming = resume_from > 0 and resp.status == 206
            if not resuming:
                resume_from = 0
            mode = "ab" if resuming else "wb"
            with open(part, mode) as f:
                while True:
                    self._check_cancelled()
                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    on_chunk(len(chunk))


# 进程内单例：下载任务本身是全局独占资源，与 model_registry 同样的单例口径
model_download_manager = ModelDownloadManager()
