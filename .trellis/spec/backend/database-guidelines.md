# 数据库约定（SQLite / aiosqlite）

> 项目只有一个本地数据库：`HistoryDB`（`app/integrated_app/history_db.py`），
> 用 `aiosqlite` 异步访问 SQLite。**没有 ORM**，全部裸 SQL + 参数绑定。

---

## 1. 存储内容

| 表 | 用途 |
|----|------|
| `history` | 修复任务历史（图片/视频），带 FTS5 全文索引 |
| `tasks` | 后台任务实时状态持久化，用于崩溃恢复 |

连接参数（初始化时设置，改动需评估并发影响）：

```python
await db.execute("PRAGMA journal_mode=WAL")     # 读写并发
await db.execute("PRAGMA synchronous=NORMAL")   # WAL 下的性能/安全折中
await db.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
```

---

## 2. 唯一写入口

所有写操作走 `_execute_write(sql, params, *, many=False, want_rowcount=False)`，读走
`_fetch_one` / `_fetch_all`。**不要**在业务代码里直接 `aiosqlite.connect` 或自己开事务。
批量插入失败会自动回退到逐条插入（鲁棒性设计，别"优化"掉）。

---

## 3. 查询写法（强制）

```python
# ✅ 参数绑定
await self._fetch_all("SELECT * FROM history WHERE id IN (?, ?)", (1, 2))

# ✅ 动态 IN 子句：占位符按长度生成，值全部走参数
placeholders = ", ".join("?" for _ in ids)
await self._fetch_all(f"SELECT * FROM history WHERE id IN ({placeholders})", tuple(ids))

# ❌ 字符串拼值（SQL 注入）
await self._fetch_all(f"SELECT * FROM history WHERE id = {record_id}")
```

动态 `ORDER BY` 必须白名单映射，不得直接把用户输入拼进 SQL。

### UPDATE 列白名单（强制）

`update_record` / `update_task` 用集合白名单校验列名，非法列直接 `raise ValueError`：

```python
allowed_columns = {"status", "output_file", "processing_time", "error_message", "parameters", ...}
invalid_keys = set(kwargs.keys()) - allowed_columns
if invalid_keys:
    raise ValueError(f"不允许更新的列: {invalid_keys}，允许的列: {allowed_columns}")
```

**新增可更新列时必须同时把它加进白名单**，否则运行时随机抛 `ValueError`。

### N+1 禁止

批量场景用 `get_records_by_ids()` 一条 `IN` 查询，不要循环 `get_record()`（历史缺陷 C3 已修）。

---

## 4. Schema 迁移（强制流程）

版本标记用 `PRAGMA user_version`，当前 `SCHEMA_VERSION = 5`。新增结构变更三步：

1. 写迁移函数，**必须幂等**（列已存在则 no-op）：

```python
async def _migrate_v5_to_v6(db: aiosqlite.Connection) -> None:
    """v5 → v6：<说明>。必须幂等：列已存在时 no-op。"""
    cursor = await db.execute("PRAGMA table_info(history)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if existing_cols and "new_col" not in existing_cols:
        await db.execute("ALTER TABLE history ADD COLUMN new_col TEXT DEFAULT ''")
```

2. 在 `_MIGRATIONS` 元组登记 `(版本号, 描述, 函数)`。
3. `SCHEMA_VERSION += 1`。

同时：首次建表语句里**也要包含新列**（新库从 v0 一步推进到最新版，但建表已含全列时迁移是 no-op）。
版本回滚（`current_version > SCHEMA_VERSION`）只告警并跳过迁移，不报错。

迁移前会自动备份（`_backup_before_migration`，空库/同版本跳过，备份失败不阻断迁移）。

---

## 5. 字段与命名

- 表名小写复数：`history`、`tasks`。
- 列名 snake_case：`input_sha256`、`output_size_bytes`、`vram_peak_mb`、`deleted_at`。
- 时间列统一存 ISO 8601 字符串（`datetime.now().isoformat()`），不做时区转换。
- 软删除用 `deleted_at`（NULL 表示未删），保留策略见 `services/output_retention.py`。
- `pinned = 1` 的记录豁免保留策略清理。
- 文件大小用 `_bytes` 后缀，显存用 `_mb` 后缀，时长用 `_sec` 后缀。
- FTS 索引靠触发器自动同步（INSERT/UPDATE/DELETE），新增可检索列需同步改触发器与
  `utils/fts.py` 的查询转义。

---

## 6. 线程 / 事件循环

- `HistoryDB` 使用持久连接，异步上下文管理（`async with`）。
- 推理线程（`asyncio.to_thread`）**不能**直接写库，必须经事件循环回调：
  参考 `services/restore_service.py` 的 `create_db_progress_persister`（进度节流落库）。
- 任务内存缓存与 DB 的关系是 cache-aside：**DB 是唯一可信源**，缓存只是读加速。
  状态修改经 `services/task_state.py` 的 `task_state_store`，禁止绕过。

---

## 7. 常见错误

| 错误 | 后果 |
|------|------|
| 新增列但忘了加进 `allowed_columns` | `update_*` 运行时 `ValueError` |
| 迁移函数不幂等 | 重复执行/旧库升级失败 |
| 直接把用户输入拼进 SQL / ORDER BY | 注入 |
| 循环单条查（N+1） | 批量 100 文件时 DB 压力放大 |
| 在推理线程里直接 await 数据库 | 跨线程访问 aiosqlite 连接异常 |
| 测试写真实 `history.db` | 用 `tests/conftest.py` 的 `test_app` fixture（`db_path` 已重定向到 `tmp_path`） |
