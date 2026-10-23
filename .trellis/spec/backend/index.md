# 后端开发规范（SeedVR2-lite）

> 本目录描述本仓库**实际存在**的后端约定，供 `trellis-implement` / `trellis-check`
> 子 agent 与人工开发者共同使用。内容以真实代码与配置文件为准，不写理想态。

---

## 项目速览

- 技术栈：Python 3.12 + FastAPI + Jinja2/HTMX + CUDA（PyTorch / diffusers）
- 入口：`app/integrated_app/app_server.py`（`create_app` / lifespan）
- 唯一配置源：仓库根 `config.yaml` → `config.py` / `config_models.py`（Pydantic）
- 存储：SQLite（`aiosqlite`，WAL），单库 `history.db`
- 测试：`tests/test_*.py`（pytest，覆盖率门禁 55%）+ `tests/specs/*.spec.ts`（Playwright）

---

## 规范索引

| 文档 | 覆盖内容 |
|------|----------|
| [目录结构](./directory-structure.md) | 分层职责、路由自动发现、依赖注入、命名约定 |
| [错误处理](./error-handling.md) | `RestoreError` 体系、全局处理器、统一响应信封、禁止写法 |
| [日志规范](./logging-guidelines.md) | `setup_logging`、级别选择、格式化、必记/禁记内容 |
| [数据库规范](./database-guidelines.md) | aiosqlite 写法、列白名单、schema 迁移流程、跨线程约束 |
| [质量规范](./quality-guidelines.md) | 工具阈值、禁止/必须模式、测试要求、门禁命令、评审清单 |

思考引导（通用方法论，非本仓特有）：
[code-reuse-thinking-guide.md](../guides/code-reuse-thinking-guide.md)、
[cross-layer-thinking-guide.md](../guides/cross-layer-thinking-guide.md)

---

## 与仓库既有治理文档的关系

本目录是 AI 会话的**速查层**；权威来源仍是仓库追踪文件，冲突时以下者为准：

| 主题 | 权威文件 |
|------|----------|
| 可移植性 / Git / 禁区 / 门禁口径 | `docs/CODING_STANDARDS.md` |
| 完成定义（DoD 分级） | `docs/DOD.md` |
| 覆盖率边界与真机质量门 | `docs/ML_OPS_GATES.md` |
| 模块边界与路由注册细则 | `docs/project/MODULE_MAP.md` |
| 工具阈值 | `pyproject.toml` |
| 提交/推送门禁接线 | `.pre-commit-config.yaml`、`.githooks/`、`.github/workflows/` |

维护约定：改了 `pyproject.toml` 的 lint/mypy/pytest 配置、`exceptions.py` 的异常体系、
`history_db.py` 的迁移机制、`utils/response.py` 的信封结构，或 `.pre-commit-config.yaml`
的门禁接线时，同步更新对应本目录文档。本目录不记录变更历史（历史进 `CHANGELOG.md`）。
