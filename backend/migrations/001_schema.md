# 初始结构

SQLAlchemy启动创建以下业务表：service_periods, service_cases, business_calendars, sla_runs, delivery_schedules, deliveries、users、audits。Source专属JSON字段由对应validation严格校验；Policy为不可覆盖的版本记录；Run保存source/policy冻结快照；Period仅用于已有期间选择器，不计入核心复杂度。已有数据库升级需先备份，后续字段演进应以独立迁移执行。

## 002 不可变内容归档（在线迁移）

冻结底稿与口径、收件箱交付内容改为内容寻址（sha256）不可变归档，相同内容全局仅一份，落在共享卷 `ARCHIVE_DIR`（默认 `/app/artifacts/archive`，两级分片）。

新增表：
- `archive_blobs`：归档对象元数据（checksum 唯一、size、ref_count、status=ok/corrupted/purged、corrupt_reason）。
- `archive_refs`：活动引用台账（blob × 业务表 × 业务记录，唯一约束）。
- `archive_ref_tombstones`：已解除引用的墓碑；迟到迁移遇到墓碑且业务记录已消失时拒绝恢复。
- `archive_migrations` / `archive_migration_items`：迁移任务与逐记录进度（pending/running/done/failed），中断按未完成条目续跑，重试幂等不重复归档。

既有表加可空列：`sla_runs.snapshot_blob_id`、`deliveries.payload_blob_id`。旧 JSON 列保留，读取统一走装载器：优先归档、回退内联，因此迁移期间新旧记录均可读，核算、复核、定时取用不受影响。应用启动与 worker 启动均调用 `ensure_schema()`，对已存在库仅补缺列、补建新表（ALTER TABLE ADD COLUMN / create_all），不改动既有数据。

回收规则：仅当 blob 无任何活动引用且 ref_count=0 时才删除文件，删除为最后一步；元数据行保留为 purged。报告/收件箱清退先删业务记录、再转墓碑并递减计数，随后 GC；与新建报告交错时由活动引用计数保护，不会误删仍被引用的归档。

损坏处理：每次读取强制校验文件大小与 sha256；失败即标记 corrupted 并记录原因，读取接口返回 409、拒绝下载残缺 CSV，列表接口以 `archive_corrupt` 标记而不是整页失败。
