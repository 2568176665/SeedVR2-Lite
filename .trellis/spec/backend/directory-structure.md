# 目录结构与分层约定

> 本文件描述 SeedVR2-lite **当前实际**的后端布局。结构以物理目录为准，
> 历史文档中的 `api/`、`core/`、`db/` 顶层目录均为早期目标结构，已不存在。

---

## 1. 顶层布局

```
SeedVR2-lite/
├── app/integrated_app/   ← 唯一主应用包（FastAPI + 全部 Web 业务代码）
├── app/models/ app/utils/ app/vram/  ← 应用侧辅助模块
├── common/               ← 通用算法 / 日志 / 分布式工具
├── model_lib/            ← vendored 上游模型源码（🚫 禁区）
├── configs_3b/ configs_7b/ ← 模型架构配置（随权重，🚫 禁区）
├── config.yaml           ← 运行时唯一配置源（仓库根级、已提交）
├── tests/                ← pytest（test_*.py）+ Playwright（tests/specs/*.spec.ts）
├── scripts/              ← 构建 / 门禁 / 迁移脚本
├── desktop/              ← Tauri v2 桌面壳（Rust + JS）
├── launcher/ portable_launcher/  ← 安装器与便携启动器
└── docs/ website/        ← 开发文档 / VitePress 官网（两套，别混淆）
```

根目录受 `.github/layout-rules.yaml` + `.github/scripts/check_layout.py` 冻结：
新增根级文件/目录会被 pre-commit 与 CI 拦下，需先登记 `root_allowlist`。
不要为了放临时文件新建根目录（`tmp_*`、`*_REPORT*.md` 等会被 `forbid_root_patterns` 直接判 FAIL）。

---

## 2. app/integrated_app 内部分层

| 目录 | 职责 | 典型文件 |
|------|------|----------|
| `app_server.py` | FastAPI 组装入口（`create_app` / lifespan / 中间件注册顺序） | `app_server.py` |
| `routes/` | HTTP 适配层：参数解析、校验、提交队列 | `routes/restore/{upload,batch,task,scan}.py`、`routes/system/{gpu,health,sse}.py` |
| `services/` | 业务编排层：状态机、OOM 降级、历史落账、断点续跑 | `restore_service.py`、`task_state.py`、`task_events.py` |
| `engines/` | 推理引擎层（模型加载 / DiT / VAE / 视频管线） | `engines/seedvr2_engine.py`、`engines/_dit_pipeline.py` |
| `optimization/` | 显存与推理优化（`gpu/`、`inference/`、`video/`、`engine/`） | `optimization/gpu/blockswap.py` |
| `middleware/` | CSRF / 限流 / 请求 ID / 追踪 / 安全头 / 统一异常 | `middleware/error_handler.py` |
| `security/` | 路径守卫、完整性清单、水印、密钥（🚫 禁区） | `security/path_guard.py` |
| `utils/` | 无状态工具（响应信封、哈希、指标、命名） | `utils/response.py` |
| `lifecycle/` | lifespan 后台循环（卡死清理 / 停滞看门狗 / 空闲卸载） | `lifecycle/background_tasks.py` |
| `locales/` | 5 语言 JSON 词表 | `locales/{zh,zh-TW,en,ja,fr}.json` |
| `static/` `templates/` | 前端资源与 Jinja2 模板 | `templates/base.html` |

### 分层铁律

- **路由层只做 HTTP**：解析/校验/预检/入队，不写编排逻辑。编排一律进 `services/`。
- **服务层禁止依赖 FastAPI**：`services/` 内不得 import `Request` / `Response` / `HTTPException`，
  领域错误抛 `RestoreError` 子类（见 `error-handling.md`）。约束写在 `services/__init__.py` 的模块 docstring 里。
- **状态修改唯一入口**：任务状态只能经 `services/task_state.py` 的 `task_state_store` 改，
  绕过会产生内存缓存与 SQLite 双真源漂移。
- **推理串行**：所有推理经 `task_queue.py` 单 worker 执行，服务层不自行开并发。

---

## 3. 路由注册：自动发现（最容易踩的坑）

`routes/__init__.py` 的 `auto_discover_routes(app)` 用 `pkgutil.iter_modules` 递归扫描
`routes` 包及其子包，凡模块内有 **module 级 `router`** 属性的即自动 `include_router`。
**不需要**手动注册，也**没有**"路由清单文件"。

```python
# app/integrated_app/routes/system/gpu.py
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["GPU信息"])
```

规则：

1. 文件放在 `routes/` 或其子包下（现有子包：`restore/`、`system/`、`ui/`）。
2. 变量名必须是 `router`，不能叫 `restore_router`。
3. `prefix` 不要与同级模块重复（两个模块都写 `prefix="/api/restore"` 必然冲突）。
4. ⚠️ **扫描期 import 失败只打 `logger.warning`，不阻断启动** → 路由静默消失
   （Swagger 查不到但服务照常启动）。排查顺序：启动日志「自动发现路由: xxx」条数 →
   「导入路由模块失败」告警 → 最后才怀疑路由代码本身。

新增前端调用的接口时，`tests/test_api_contract.py` 会比对前端引用的 `/api/...` 与后端注册表，
后端缺失直接变红（历史事故：`cancelBatch()` 打向一个从未注册的路径，404 被 `.catch` 吞掉）。

---

## 4. 组件获取：依赖注入

路由不直接摸 `request.app.state`，统一走 `dependencies.py`：

```python
from app.integrated_app.dependencies import get_config, get_task_queue

@router.post("/batch")
async def create_batch(
    task_queue: TaskQueue = Depends(get_task_queue),
    config: dict = Depends(get_config),
): ...
```

新增全局组件时：在 `create_app()` 挂到 `app.state.<name>`，再在 `dependencies.py` 加 `get_<name>()`。

---

## 5. 命名约定

| 对象 | 约定 | 例 |
|------|------|-----|
| Python 模块 | snake_case，路由文件**不要求** `_router.py` 后缀 | `bad_case_retry.py`、`history_db.py` |
| 引擎管线 mixin | 下划线前缀表示"内部实现片段，不单独实例化" | `_dit_pipeline.py`、`_vae_pipeline.py` |
| 测试 | `tests/test_<被测模块>.py`；`post_edit_gate.py` 靠此精确命中定向用例 | `tests/test_cache.py` |
| 前端 e2e | `tests/specs/<功能>.spec.ts` | `tests/specs/history.spec.ts` |
| 常量 | 模块级 UPPER_SNAKE，可带语义前缀 | `DEFAULT_QUEUE_MAXSIZE`、`SCHEMA_VERSION` |
| 中文文件名 | 仅 `docs/` 存量文档保留；新文档优先英文 kebab-case | `docs/CODING_STANDARDS.md` |

科学计算 / 张量代码允许 ML 惯例命名（`B/C/H/W/T`、`F`、`l`），
`pyproject.toml` 的 `[tool.ruff.lint.per-file-ignores]` 已按目录放开，**不要**为此重命名张量维度。

---

## 6. 前端放置

- 页面模板进 `templates/`，静态资源进 `static/{css,js}/`，设计规范见 `static/design-system.md`。
- 技术栈是 Jinja2 + HTMX + 原生 JS，**不是** SPA 框架；不要引入构建期前端框架。
- 新增 UI 文案必须同步 `locales/` 下 5 个 JSON，并给键名（点号分层，如 `nav.video_restore`）。

---

## 7. 参考实现（新代码照抄这些）

| 场景 | 参考文件 |
|------|----------|
| 新增一组 API 路由 | `routes/system/gpu.py`（含 Depends、文档字符串、统一响应） |
| 新增业务编排 | `services/restore_service.py`（服务层无 FastAPI 依赖） |
| 新增磁盘/显存前置检查 | `services/restore_service.py` 的 `ensure_disk_space`、`vram_preflight_gate` |
| 新增 SQLite 表/列 | `history_db.py`（迁移表 + 列白名单） |
| 新增中间件 | `middleware/`，注册顺序见 `app_server.py` 尾部（LIFO，最外层最后添加） |
