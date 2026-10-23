# 日志约定

> 统一入口是 `setup_logging()`（`app_server.py`），模块内一律
> `logger = logging.getLogger(__name__)`，**不要**用 `print()`，也不要自建 handler。

---

## 1. 配置来源

`setup_logging(config)` 在应用启动时调用一次，`force=True` 覆盖既有配置：

| 项 | 来源 | 默认 |
|----|------|------|
| 级别 | `LOG_LEVEL` 环境变量 > `config.yaml` 的 `logging.level` | `INFO` |
| 文件 | `LOG_PATH` 环境变量 > `logging.file` | `logs/app.log`（项目根下） |
| 轮转 | `logging.max_size_mb` / `logging.backup_count` | 50MB × 3 |
| 格式 | `LOG_FORMAT=json` 切 `JSONLogFormatter` | `text` |

- 双通道：stdout + `RotatingFileHandler(encoding="utf-8")`。
- 文件日志初始化失败**降级为仅控制台**（并把控制台提到 WARNING），不阻断启动。
- `common/logger.py` 的 `get_logger()` 只用于 `model_lib/` / `common/` 侧（分布式 rank 感知），
  `app/integrated_app/` 内**不要**用它。

### text 格式

```
[%(asctime)s] [%(levelname)s] [PID:%(process)d TID:%(thread)d] [%(name)s:%(filename)s:%(lineno)d] [req=%(request_id)s] [trace=%(trace_id)s] %(message)s
```

`request_id` / `trace_id` 由 `RequestIDLogFilter` / `TraceLogFilter` 自动注入，
业务代码不需要手动拼。

---

## 2. 级别选择

| 级别 | 用于 | 示例（本仓真实场景） |
|------|------|----------------------|
| `DEBUG` | 内部状态、可关闭的诊断 | `logger.debug("核心模块完整性自检跳过: %s", e)` |
| `INFO` | 生命周期里程碑、配置生效、任务状态迁移 | 启动/关闭、`历史数据库已初始化`、迁移 `schema v2 → v3` |
| `WARNING` | 可降级、可自愈、不中断服务 | `RestoreError [code]`（业务异常）、FFmpeg 不可用、预留清理失败 |
| `ERROR` | 功能失败但服务存活 | `logger.error("VRAM 估算失败: %s", e)` |
| `exception` | 未预期异常，需要堆栈 | 全局兜底处理器 `logger.exception(...)` |

判断标准：**用户是否受影响**。受影响但已降级 = WARNING；受影响且该功能不可用 = ERROR；
完全没预料到 = `logger.exception`。

---

## 3. 格式化写法

仓库内两种风格并存（f-string 568 处 / `%s` 参数化 242 处），**新代码用参数化**：

```python
logger.info("恢复未完成任务失败: %s", exc)          # ✅ 延迟格式化，DEBUG 关闭时零开销
logger.info(f"恢复未完成任务失败: {exc}")           # ⚠️ 存量写法，可读性尚可，不要新增
```

例外：消息本身是纯静态字符串或只有一处拼接且不在热路径，f-string 仍可接受；
但**不要**在长循环/每帧路径里用 f-string 构造日志（视频逐帧处理会放大开销）。

异常记录：

- 吞掉异常但继续运行 → `logger.warning("...: %s", exc)`（带上下文，不带堆栈）
- 需要保留堆栈 → `logger.exception("...")` 或 `logger.warning("...", exc_info=True)`
- 绝不 `logger.error(exc)` 直接传异常对象（会丢失 traceback 且消息为 str(exc)）

---

## 4. 必须记录的事件

- 应用启动/关闭、版本、监听地址与端口。
- 配置加载与校验失败（含回退到原始 YAML 的动作）。
- 模型加载/卸载/切换、显存不足降级决策（OOM 降级参数变化要留档）。
- 任务状态迁移（pending → processing → completed/failed/cancelled）与取消原因。
- 安全事件：PathGuard 拒绝、Basic Auth 失败、限流命中、CSRF 校验失败、完整性自检失败。
- 数据迁移：`history` schema 版本推进与每步迁移描述。
- 配额/水位：磁盘预检、保留策略清理条数。

---

## 5. 绝不能记录

| 内容 | 原因 |
|------|------|
| 密钥 / token / 密码 / 私钥 / 水印密钥 | 落盘即泄露；`.env` 内容不入日志 |
| 用户输入原文（尤其校验失败时的表单/文件内容） | 隐私 + 注入风险；只记 `loc/msg/type` |
| 完整本机绝对路径中的用户名部分 | 隐私；必要时用相对路径或脱敏 |
| 大体积二进制/张量/帧数据 | 日志膨胀 |
| 客户端 IP 明文长期保留 | 限流日志用聚合计数，不逐条落库 |

---

## 6. 反模式

- `print()` 到 stdout 当日志（现存 2 处 `print` 在 `optimization/webui_enhancement.py`、
  `security/integrity_selfcheck.py`，属历史遗留，不要模仿）。
- 在模块 import 期创建 handler / 调 `basicConfig`（会与 `setup_logging` 打架）。
- 同一事件在多层重复记录同一异常（路由记一次、服务记一次、引擎再记一次）→ 日志噪声翻倍。
  约定：**谁捕获谁记录**，上层只做转换不重复打。
- 用 `logger.info` 打循环内每帧/每文件进度（SSE + `metrics` 已负责进度上报）。
