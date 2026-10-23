# 错误处理约定

> 本项目的错误处理有**三层固定结构**：领域异常类 → 全局异常处理器 → 统一响应信封。
> 所有新代码必须落进这套结构，不要另起一套。

---

## 1. 领域异常：`app/integrated_app/exceptions.py`

所有业务异常继承 `RestoreError`，它提供三个契约成员：

```python
class RestoreError(Exception):
    code: str = "RESTORE_ERROR"          # 业务错误码，前端据此分支
    def __init__(self, message="...", *, code=None, detail: dict | None = None)
    @classmethod
    def http_status(cls) -> int: ...     # 子类覆盖，决定 HTTP 状态码
    def to_dict(self) -> dict: ...
```

子类模式（照抄 `DiskSpaceError`）：

```python
class DiskSpaceError(RestoreError):
    """磁盘剩余空间不足。"""

    code = "INSUFFICIENT_DISK"

    def __init__(self, message: str = "磁盘剩余空间不足", *, detail: dict | None = None):
        super().__init__(message, detail=detail)

    @classmethod
    def http_status(cls) -> int:
        return 507
```

现有异常与状态码映射（新增前先看有没有能复用的）：

| 异常 | code | HTTP |
|------|------|------|
| `ModelLoadError` / `ModelUnloadError` | `MODEL_LOAD_FAILED` / `MODEL_UNLOAD_FAILED` | 503 |
| `InsufficientVRAMError` / `InsufficientVramError` | `INSUFFICIENT_VRAM` | 422 / 503（后者为任务前预检门禁，带降档建议） |
| `InsufficientRAMError` | `INSUFFICIENT_RAM` | 422 |
| `DiskSpaceError` | `INSUFFICIENT_DISK` | 507 |
| `TaskQueueFullError` | `TASK_QUEUE_FULL` | 503（快速拒绝 + `Retry-After`） |
| `ModelFileNotFoundError` | `MODEL_FILE_NOT_FOUND` | 404 |
| `ConfigError` | `CONFIG_ERROR` | 400 |
| `InferenceCancelledError` | `INFERENCE_CANCELLED` | 400 |
| `InferenceError` / `BlockSwapError` / `VAEDecodeError` / `VAEEncodeError` | `*_FAILED` | 500 |

> 注意 `InsufficientVRAMError`（422，抛在推理过程中）与 `InsufficientVramError`（503，抛在
> 任务提交前的 `vram_preflight_gate`）是两个不同异常，名字只差大小写，别用错。

---

## 2. 全局异常处理器：`middleware/error_handler.py`

`register_error_handlers(app)` 按类型注册，**兜底 `Exception` 必须最后注册**（FastAPI 按注册顺序匹配）：

```
RestoreError → StarletteHTTPException → RequestValidationError
→ ValueError / MemoryError / FileNotFoundError → Exception（兜底）
```

所有处理器最终收敛到 `_error_response()`，它按请求类型分流：

- **普通 API 请求** → `respond_error()` JSON 信封
- **HTMX 请求**（`HX-Request: true`）→ 空响应体 + `HX-Trigger` 头触发前端 Toast

### 安全硬约束

- 兜底处理器**不得**回传异常类型、堆栈、内部路径（防框架指纹识别），
  统一 `"服务器内部错误，请稍后重试"`；详情只进 `logger.exception`。
- `RequestValidationError` 只回传 `loc / msg / type`，**丢弃 `input`**（防大体积输入与敏感字段回显）。
- 错误 `message` 面向用户，不含路径、SQL、堆栈。

---

## 3. 统一响应信封

唯一出口是 `utils/response.py`：

```python
from app.integrated_app.utils.response import respond_success, respond_error

return respond_success({"task_id": task_id})                    # {"success": true, "data": {...}}
return respond_error(code="PATH_NOT_ALLOWED", message="路径不在允许范围内", status=403)
# {"success": false, "error": {"code": ..., "message": ..., "detail": ...}}
```

规则：

- 路由**不要**直接 `JSONResponse({...})` 拼业务信封。现状：`routes/` 下仍有 26 处直接
  `JSONResponse`，集中在 `system/settings.py`（多数手拼 `{"success": True, "message": ...}`）、
  `system/gpu.py`、`system/readiness.py`、`system/health.py`、`restore/scan.py`。
  这些是历史写法，**不要模仿**；新增业务端点一律走 `respond_success` / `respond_error`
  （探针/健康检查这类无信封语义的裸结构除外）。
- 业务错误**优先 raise 领域异常**，让全局处理器统一转换；只有在路由层就很清楚该返回什么
  状态码且无需全局语义时才直接 `respond_error`。
- API 404 也进信封（`routes/__init__.py` 的 404 handler 已统一处理），页面 404 才重定向 `/`。

---

## 4. 禁止的写法

| 反模式 | 为什么 |
|--------|--------|
| `except: pass` / `except Exception: pass` | DoD 明令禁止"静默吞错"。`app/integrated_app` 中现存 44 处 `pass` 分支属历史负担，新增代码不得增加 |
| 裸 `except Exception` 不加标识 | 若确实必须兜底，写 `except Exception as e:  # noqa: BLE001 — <理由>`，并 `logger.warning/exception` 留痕（参考 `routes/restore/common.py`） |
| 把 `HTTPException` 抛进服务层 | 服务层禁止依赖 FastAPI；用领域异常，否则单测必须构造 Request 才能验证 |
| 在 `finally` 里吞掉原始异常 | 原始因果会丢；用 `raise ... from e` |
| 错误消息里拼入本机路径 / 用户输入原文 | 信息泄露；详情放 `detail` 且只给结构化字段 |

---

## 5. 与前端/HTMX 的交互

- 普通 `fetch` 调用：解析 `error.code` 做分支，`error.message` 直接展示。
- HTMX `hx-post`：后端返回 `HX-Trigger: {"showToast": {"message": ..., "type": "error"}}`，
  空 body。前端不需要自己判断状态码。
- SSE 端点（`routes/restore/task.py`）：错误以 `data: {"error": "..."}` 事件推入流内，
  不走信封（流已建立，无法改状态码）。

---

## 6. 验证方式

- 新增异常类 → 在 `tests/test_exceptions.py` 加断言 `code` / `http_status()` / `to_dict()` 的用例。
- 新增路由的错误分支 → 用 `tests/conftest.py` 的 `test_app` fixture + `csrf_post()` helper 验证状态码与 `error.code`。
- 信封结构与处理器行为 → `tests/test_error_handler.py`、`tests/test_response.py`；
  改动 `middleware/error_handler.py` 或 `utils/response.py` 后两个都必须跑。
- 新增响应体结构变更 → `tests/test_api_schema.py` 会拉 OpenAPI schema 对比。
