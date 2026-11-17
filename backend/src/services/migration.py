"""历史内联 JSON → 不可变归档的在线迁移。

- 迁移期间系统照常核算、复核、定时取用；新旧记录均走统一装载器读取
- 原子认领（条件 UPDATE）保证同一记录不会被两个 worker 并发迁移
- 逐记录建 item 并提交，中断后按 pending/running 续跑；重试幂等，绝不重复归档
- 记录若已被页面清退（引用墓碑存在且业务记录已消失），迁移拒绝恢复引用
"""
import time
from sqlalchemy import text
from src.models.run import Run,Delivery
from src.models.archive import ArchiveMigration,MigrationItem,ArchiveBlob
from src.services import archive_store,archive
from src.services.audit import log

STALE_SECONDS=30
BATCH=200

def start(db,user,scope='all'):
 """创建迁移任务并登记待迁记录；重复点击复用最近一个未完成任务。"""
 now=time.time()
 existing=db.query(ArchiveMigration).filter(ArchiveMigration.status.in_(['queued','running'])).order_by(ArchiveMigration.id.desc()).first()
 if existing: return existing
 m=ArchiveMigration(scope=scope,status='queued',created=now,updated=now)
 db.add(m);db.flush()
 specs=[]
 if scope in ('all','runs'):
  specs += [('sla_runs',r.id) for r in db.query(Run).filter(Run.snapshot_blob_id.is_(None),Run.snapshot.isnot(None)).all()]
 if scope in ('all','deliveries'):
  specs += [('deliveries',d.id) for d in db.query(Delivery).filter(Delivery.payload_blob_id.is_(None),Delivery.payload.isnot(None)).all()]
 for table,rid in specs:
  # 唯一约束兜底：同一记录不会被登记两次；failed 条目可被新任务重试
  old=db.query(MigrationItem).filter_by(record_table=table,record_id=rid).first()
  if old is None:
   db.add(MigrationItem(migration_id=m.id,record_table=table,record_id=rid,state='pending',updated=now))
  elif old.state in ('failed','running'):
   old.migration_id=m.id;old.state='pending';old.error='';old.updated=now
 m.total=db.query(MigrationItem).filter_by(migration_id=m.id).count()
 log(db,user.email,'archive.migration.start',{'id':m.id,'total':m.total,'scope':scope})
 db.commit()
 import redis
 from src.config.settings import REDIS_URL
 redis.Redis.from_url(REDIS_URL).rpush('archive-jobs',m.id)
 return m

def _migrate_one(db,item):
 item.updated=time.time()
 if item.record_table=='sla_runs':
  obj=db.get(Run,item.record_id);kind='run_snapshot'
  db.refresh(obj) if obj else None           # 认领提交后丢弃旧快照，读取最新外键状态
  data=obj.snapshot if obj else None
 else:
  obj=db.get(Delivery,item.record_id);kind='delivery_payload'
  db.refresh(obj) if obj else None
  data=obj.payload if obj else None
 if obj is None:
  item.state='done';item.error='记录已删除，跳过';db.flush();return 'skipped'
 # 已归档（重试/重复任务）：直接幂等完成，不重复写 blob
 blob_id=obj.snapshot_blob_id if item.record_table=='sla_runs' else obj.payload_blob_id
 if blob_id:
  known=db.get(ArchiveBlob,blob_id)
  item.state='done';item.checksum=known.checksum if known else '';db.flush();return 'skipped'
 if data is None:
  item.state='done';item.error='无内联内容，跳过';db.flush();return 'skipped'
 blob=archive_store.put(db,data,kind)
 # 墓碑保护：该引用已被页面清退时，拒绝恢复，迁移记失败但不破坏清退结果
 try:
  archive.add_ref(db,blob,item.record_table,obj.id,kind)
 except Exception as exc:
  item.state='failed';item.error=str(getattr(exc,'detail',exc));return 'failed'
 if item.record_table=='sla_runs': obj.snapshot_blob_id=blob.id
 else: obj.payload_blob_id=blob.id
 item.checksum=blob.checksum;item.state='done'
 # 引用与 blob 落定后才释放内联副本，保证任何时刻至少一份可读数据
 db.flush()
 if item.record_table=='sla_runs': obj.snapshot=None
 else: obj.payload=None
 return 'migrated'

def _claim_item(db,migration_id):
 """原子认领一条待迁记录：先取候选 id，再按 id 条件更新，影响行数为 1 才算认领成功。
 PG 下被行锁串行化；SQLite 下库级写锁同样保证并发 worker 只有一个成功。
 running 为认领中间态，worker 中断后由 sweep_stale 重置续跑，不会漏记录。"""
 now=time.time()
 row=db.execute(text("""SELECT id FROM archive_migration_items
  WHERE migration_id=:migration_id AND state='pending' ORDER BY id LIMIT 1"""),
  {'migration_id':migration_id}).first()
 if not row: return None
 candidate=row[0]
 if db.bind.dialect.name=='postgresql':
  res=db.execute(text("""UPDATE archive_migration_items SET state='running',attempts=attempts+1,updated=:now
   WHERE id=:id AND state='pending' RETURNING id"""),{'now':now,'id':candidate})
  item_id=res.scalar()
 else:
  res=db.execute(text("""UPDATE archive_migration_items SET state='running',attempts=attempts+1,updated=:now
   WHERE id=:id AND state='pending'"""),{'now':now,'id':candidate})
  item_id=candidate if res.rowcount==1 else None
 db.commit()
 return db.get(MigrationItem,item_id) if item_id else None

def run_migration(db,m,worker_name):
 m.status='running';m.worker=worker_name;m.heartbeat=time.time();m.updated=time.time();db.commit()
 while True:
  item=_claim_item(db,m.id)
  if item is None: break
  try:
   outcome=_migrate_one(db,item)
   col='migrated' if outcome=='migrated' else 'skipped'
   # 用 Core 语句累加计数，避免 ORM 对象在多次提交间状态漂移
   db.execute(text('UPDATE archive_migrations SET %s=%s+1,last_record_table=:t,last_record_id=:i,updated=:now WHERE id=:mid'%(col,col)),
    {'t':item.record_table,'i':item.record_id,'now':time.time(),'mid':m.id})
   db.commit()
  except Exception as exc:
   db.rollback()
   fresh=db.get(MigrationItem,item.id)
   if fresh is None: continue
   msg=str(exc).lower()
   transient=any(k in msg for k in ('locked','deadlock','try restarting transaction','timeout'))
   if transient and fresh.attempts<10:
    # 瞬时锁冲突：退回待认领，留给后续轮次，不计失败、不漏记录
    fresh.state='pending';db.commit()
   elif fresh.state=='running':
    fresh.state='failed';fresh.error=str(exc)
    db.execute(text('UPDATE archive_migrations SET failed=failed+1,updated=:now WHERE id=:mid'),{'now':time.time(),'mid':m.id})
    db.commit()
  db.execute(text('UPDATE archive_migrations SET heartbeat=:now WHERE id=:mid'),{'now':time.time(),'mid':m.id});db.commit()
  db.expire_all()   # 丢弃身份映射缓存，下一条目从已提交状态重新装载
 m=db.get(ArchiveMigration,m.id)
 open_items=db.query(MigrationItem).filter_by(migration_id=m.id).filter(MigrationItem.state.in_(['pending','running'])).count()
 m.status='done' if open_items==0 and m.failed==0 else ('failed' if m.failed else 'running')
 m.updated=time.time();db.commit()
 if m.status in ('done','failed'):
  log(db,worker_name,'archive.migration.%s'%m.status,{'id':m.id,'migrated':m.migrated,'failed':m.failed})
 return m

def sweep_stale(db,worker_name):
 """接管心跳超时的任务（worker 中断续跑），并兜底处理没有活任务认领的遗留条目。"""
 now=time.time()
 # 中断 worker 留下的 running 条目重置为 pending，保证不漏
 db.execute(text("UPDATE archive_migration_items SET state='pending' WHERE state='running'"))
 for m in db.query(ArchiveMigration).filter(ArchiveMigration.status.in_(['queued','running'])).all():
  if m.status=='queued' or now-(m.heartbeat or 0)>STALE_SECONDS:
   run_migration(db,m,worker_name)

def progress(db,migration_id=None):
 q=db.query(ArchiveMigration)
 m=q.filter_by(id=migration_id).first() if migration_id else q.order_by(ArchiveMigration.id.desc()).first()
 if not m: return None
 items=db.query(MigrationItem).filter_by(migration_id=m.id).all()
 return {
  'id':m.id,'scope':m.scope,'status':m.status,'total':m.total,
  'migrated':m.migrated,'skipped':m.skipped,'failed':m.failed,
  'worker':m.worker,'heartbeat':m.heartbeat,'error':m.error,'updated':m.updated,
  'failures':[{'table':i.record_table,'record_id':i.record_id,'error':i.error,'attempts':i.attempts} for i in items if i.state=='failed'],
 }
