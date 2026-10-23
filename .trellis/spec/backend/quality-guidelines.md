# 质量约定

> 本文件是**可执行**的质量底线：格式规则、禁止模式、测试要求、门禁命令。
> 权威来源：`pyproject.toml`、`.pre-commit-config.yaml`、`.github/workflows/ci.yml`、
> `docs/CODING_STANDARDS.md`、`docs/DOD.md`。冲突时以配置文件为准。

---

## 1. 工具与阈值（单一事实来源：`pyproject.toml`）

| 工具 | 版本 | 配置 |
|------|------|------|
| Python | `>=3.12,<3.15` | `requires-python` |
| ruff | `0.16.10` | `line-length=120`，`select = E,F,W,I,N,UP,B,A,C4,SIM`，`ignore = E501,B008,SIM108` |
| black | `26.10.0` | `line-length=120`，`target-version=py312` |
| mypy | `2.4.0` | `strict=false`，`check_untyped_defs=true`，exclude `^model_lib/` |
| pytest | `>=8.0` | `testpaths=["tests"]`，`asyncio_mode="auto"`，`--strict-markers --tb=short --timeout=60` |
| 覆盖率 | `fail_under=55` | 只统计 `app/integrated_app` |

**格式只走 black**：不要在 pre-commit 里加 `ruff-format`——它和 black 折行规则分歧，
会导致"提交时被 ruff 改写、push 时被 black 拒绝"来回翻转（已知问题 #61）。

`pyproject.toml` 的 `[tool.ruff.lint.per-file-ignores]` 已按目录放开 ML 命名
（`model_lib/**`、`app/models/**`、`optimization/**` 等）。**不要**为此重命名张量维度，
也不要顺手扩大 ignore 范围。

---

## 2. 禁止模式

| 禁止 | 替代 | 依据 |
|------|------|------|
| `print()` 当日志 | `logger.info(...)` | 现存 2 处为历史遗留 |
| `except: pass` / 静默吞错 | 记日志并明确降级；必须兜底时加 `# noqa: BLE001 — <理由>` | `docs/DOD.md` §6 |
| 硬编码本机绝对路径 | `Path(__file__).resolve().parents[N]`、`$PSScriptRoot`、`%~dp0` | `docs/CODING_STANDARDS.md` §1 |
| 路由里写业务编排 | 迁到 `services/` | 分层铁律 |
| 服务层 import FastAPI | 抛 `RestoreError` 子类 | `services/__init__.py` |
| 手写 `include_router` | 放文件 + module 级 `router` | 自动发现 |
| 直接 `JSONResponse({...})` 拼业务信封 | `respond_success` / `respond_error` | `error-handling.md` |
| 新增根目录/根文件 | 登记 `.github/layout-rules.yaml` 的 `root_allowlist` | `check_layout.py` |
| 改配置只改 `config.yaml` | 同步 `config_models.py` + `config.py`，跑 `check_config_refs.py` | 配置门禁 |
| 新增 UI 文案只改一个语言 | 同步 `locales/` 5 个 JSON | `docs/DOD.md` §4 |
| `git commit --no-verify` 绕过门禁 | 改代码；紧急绕过需所有者授权并说明 | `docs/CODING_STANDARDS.md` §5.2 |

### 禁区目录（改动需人工授权）

`model_lib/`、`app/integrated_app/security/`、`config.yaml`（+ `config.py` / `config_models.py`）、
`configs_3b/`、`configs_7b/`。详见 `docs/CODING_STANDARDS.md` §5.1。

---

## 3. 必须模式

- **单一配置源**：运行时读 `config.yaml`；新代码用 `get_app_config()`（Pydantic）而非裸 dict，
  路由内已注入的 `config: dict` 可继续用，但新增字段要先进 `config_models.py`。
- **依赖注入**：全局组件走 `dependencies.py`，不直接摸 `app.state`。
- **路径安全**：任何用户指定路径的文件读/扫/下载必须经 `security/path_guard.py`；
  上传必过 `security/magic_check.validate_upload_magic`。
- **类型标注**：新函数标注参数与返回类型。`dict` 泛用处至少写 `dict[str, Any]`。
- **常量集中**：`DEFAULT_*` / `MAX_*` 放模块级并带 docstring，不要在函数内埋魔法数。
- **中文 docstring + 英文标识符**：仓库现状是中文说明、英文代码；新代码保持一致。

---

## 4. 测试要求

- 新/改函数必须有对应 `tests/test_<模块>.py` 用例，覆盖正常 + 边界 + 异常。
- **命名决定定向验证**：`scripts/post_edit_gate.py` 用 `tests/test_<模块名>.py` 精确命中，
  其次 `test_*<模块名>*.py` 模糊命中。改了 `history_db.py` 却写成 `test_db.py` 会退化成全量跑。
- GPU 相关模块（`engines/`、`optimization/gpu/`、`vram/`）必须 mock，避免 CI 全 skip。
- FastAPI 端点用 `tests/conftest.py` 的 `test_app` fixture；写操作要带 CSRF（`csrf_post()` helper）。
- 标记：`@pytest.mark.integration`、`@pytest.mark.benchmark`（`--strict-markers` 下拼错即失败）。
- 断言要"能失败"：写测试时心算——把被测功能删掉，用例还绿吗？绿就是同义反复。

覆盖率边界（不要试图扩大）：`engines/`、`model_lib/`、GPU-only 路径已在
`[tool.coverage.run].omit` 中排除，理由见 `docs/ML_OPS_GATES.md`。真实引擎回归靠
`gpu-smoke.yml` 真机双层门 + `scripts/verify_engine.py`，不是单测。

---

## 5. 门禁命令（按成本从低到高）

```bash
# 编辑后（秒级，pre-commit 自动跑）
python scripts/post_edit_gate.py

# 静态
python -m ruff check .
python -m black --check .
python scripts/mypy_gate.py mypy app/integrated_app

# 定向测试
python -m pytest tests/test_<模块>.py -q

# 全量 + 覆盖率（分钟级，pre-push / CI）
python -m pytest -q --ignore=tests/perf --cov=app/integrated_app --cov-fail-under=55

# 结构 / 契约 / 配置门禁
python .github/scripts/check_layout.py
python -m pytest tests/test_api_contract.py -q
python scripts/audit_api_consistency.py all
python scripts/check_config_refs.py
python scripts/check_no_hardcoded_paths.py --all
python scripts/check_local_only_refs.py --all
```

**默认只跑受影响的定向检查。** 全量测试、e2e、性能、网络相关检查需显式授权。
`--no-verify` 绕过不被接受。

### 证据绑定

文档/注释里出现的可执行路径必须真实存在。追踪文件提到未随仓库分发的本地文件时，
就地补可获取性说明——`scripts/check_local_only_refs.py --all` 会校验。

---

## 6. 代码评审清单

- [ ] 改动是否只落在任务范围内？有无顺手重构无关代码？
- [ ] 是否复用了已有实现（`utils/`、`services/`、`security/`）而非新写一份？
- [ ] 分层是否守住：路由只做 HTTP，服务层不碰 FastAPI，状态只经 `task_state_store`？
- [ ] 错误是否走 `RestoreError` + 全局处理器？有无新增静默吞错？
- [ ] 用户路径是否过 `PathGuard` / `magic_check`？
- [ ] 配置新增是否同步 `config_models.py` 并跑 `check_config_refs.py`？
- [ ] 新增 UI 文案是否 5 语言同步？
- [ ] 测试是否覆盖新增分支？命名能否被 `post_edit_gate.py` 精确命中？
- [ ] 是否触碰禁区目录？若触碰，PR 是否单列说明 + 补测试？
- [ ] 文档是否需要同步（`docs/project/MODULE_MAP.md`、`CHANGELOG.md`、本 spec）？

---

## 7. 提交前

- 提交信息用 Conventional Commits：`feat: / fix: / chore: / docs: / refactor: / test:`，内容中文。
- PR 需 DCO：`git commit -s`，身份用 noreply 邮箱。
- `git status` 只应含本次改动；不要 `git add -A`；不要 stash/重置用户未提交的改动。
- CI 通过才算完成；`main` 变红且 30–60 分钟无根因时先 `git revert` 止损。
