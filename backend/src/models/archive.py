from sqlalchemy import Column,Integer,String,JSON,Float,Text,Boolean,Index,UniqueConstraint
from src.config.database import Base

class ArchiveBlob(Base):
 """内容寻址的不可变归档：相同 sha256 全局仅一份。"""
 __tablename__='archive_blobs'
 id=Column(Integer,primary_key=True)
 kind=Column(String(20),nullable=False)          # run_snapshot / delivery_payload
 checksum=Column(String(64),nullable=False,unique=True)
 size=Column(Integer,nullable=False,default=0)
 ref_count=Column(Integer,nullable=False,default=0)   # 活动引用数（不含已释放墓碑）
 status=Column(String(20),nullable=False,default='ok') # ok / corrupted / purged
 corrupt_reason=Column(Text,default='')
 created=Column(Float);last_verified=Column(Float)

class ArchiveRef(Base):
 """活动引用台账：报告/收件箱对归档的最后一处引用解除前 blob 不可回收。
 仅保存活动引用；释放后转入 ArchiveTombstone，唯一键不再被历史占用。"""
 __tablename__='archive_refs'
 __table_args__=(UniqueConstraint('blob_id','ref_table','ref_id',name='uq_archive_ref'),)
 id=Column(Integer,primary_key=True)
 blob_id=Column(Integer,nullable=False)
 ref_table=Column(String(30),nullable=False)    # sla_runs / deliveries
 ref_id=Column(Integer,nullable=False)
 kind=Column(String(20),nullable=False)
 created=Column(Float)

class ArchiveTombstone(Base):
 """已解除引用的墓碑：迟到的旧迁移不能恢复已清退引用。
 若同 id 已被全新业务记录占用，则视为新一代引用，墓碑仅作历史留存。"""
 __tablename__='archive_ref_tombstones'
 __table_args__=(Index('ix_tombstone_ref','ref_table','ref_id'),)
 id=Column(Integer,primary_key=True)
 blob_id=Column(Integer,nullable=False)
 ref_table=Column(String(30),nullable=False)
 ref_id=Column(Integer,nullable=False)
 kind=Column(String(20),nullable=False)
 created=Column(Float);released_at=Column(Float,nullable=False)

class ArchiveMigration(Base):
 """迁移任务与逐条进度；中断后按未完成记录续跑，重试不重复归档。"""
 __tablename__='archive_migrations'
 id=Column(Integer,primary_key=True)
 scope=Column(String(30),nullable=False,default='all') # all / runs / deliveries
 status=Column(String(20),nullable=False,default='queued') # queued/running/done/failed
 total=Column(Integer,default=0);migrated=Column(Integer,default=0);skipped=Column(Integer,default=0);failed=Column(Integer,default=0)
 last_record_table=Column(String(30),default='');last_record_id=Column(Integer,default=0)
 worker=Column(String(120),default='');heartbeat=Column(Float,default=0)
 error=Column(Text,default='');created=Column(Float);updated=Column(Float)

class MigrationItem(Base):
 """逐记录迁移状态，唯一约束保证并发/重试只处理一次。"""
 __tablename__='archive_migration_items'
 __table_args__=(UniqueConstraint('record_table','record_id',name='uq_migration_item'),
   Index('ix_migration_item_state','state'))
 id=Column(Integer,primary_key=True)
 migration_id=Column(Integer,nullable=False)
 record_table=Column(String(30),nullable=False)
 record_id=Column(Integer,nullable=False)
 state=Column(String(20),nullable=False,default='pending') # pending/running/done/failed
 checksum=Column(String(64),default='');error=Column(Text,default='')
 attempts=Column(Integer,default=0);updated=Column(Float)
