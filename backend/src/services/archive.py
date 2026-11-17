"""归档引用台账、垃圾回收与读取装载。

安全要点：
- add_ref 先锁定 blob 行、登记活动引用后再增加计数；GC 与新建报告交错时，
  仍被引用的归档不会被误删。
- 释放写入独立墓碑表；迟到的旧迁移碰到墓碑且业务记录确已消失时被拒绝，
  不能恢复已清退引用。同 id 被全新业务记录占用时视为新一代引用，墓碑仅留存。
- GC 只回收 ref_count=0 且无活动台账行的 blob，删除为最后一步。
"""
import time
from sqlalchemy import text
from fastapi import HTTPException
from src.models.archive import ArchiveBlob,ArchiveRef,ArchiveTombstone
from src.models.run import Run,Delivery
from src.services import archive_store

def _lock_blob(db,blob_id):
 if db.bind.dialect.name=='postgresql':
  db.execute(text('SELECT id FROM archive_blobs WHERE id=:i FOR UPDATE'),{'i':blob_id})
 return db.query(ArchiveBlob).filter_by(id=blob_id).with_for_update().first()

def _business_record_exists(db,ref_table,ref_id):
 model=Run if ref_table=='sla_runs' else Delivery
 return db.get(model,ref_id) is not None

def add_ref(db,blob,ref_table,ref_id,kind):
 """登记活动引用。墓碑指向的业务记录已消失则拒绝（迟到迁移不得恢复清退引用）。"""
 tomb=db.query(ArchiveTombstone).filter_by(ref_table=ref_table,ref_id=ref_id).order_by(ArchiveTombstone.id.desc()).first()
 if tomb is not None and not _business_record_exists(db,ref_table,ref_id):
  raise HTTPException(409,'该引用已清退，不能恢复已解除的归档引用')
 existing=db.query(ArchiveRef).filter_by(blob_id=blob.id,ref_table=ref_table,ref_id=ref_id).first()
 if existing is not None: return existing
 locked=_lock_blob(db,blob.id)
 ref=ArchiveRef(blob_id=locked.id,ref_table=ref_table,ref_id=ref_id,kind=kind,created=time.time())
 db.add(ref);db.flush()
 locked.ref_count=(locked.ref_count or 0)+1
 return ref

def release_ref(db,ref_table,ref_id):
 """解除一处引用（幂等）：活动行转墓碑；最后一处引用解除后 blob 才具备回收条件。"""
 refs=db.query(ArchiveRef).filter_by(ref_table=ref_table,ref_id=ref_id).all()
 now=time.time();changed=[]
 for ref in refs:
  blob=_lock_blob(db,ref.blob_id)
  db.add(ArchiveTombstone(blob_id=ref.blob_id,ref_table=ref_table,ref_id=ref_id,kind=ref.kind,created=ref.created,released_at=now))
  blob.ref_count=max(0,(blob.ref_count or 0)-1)
  db.delete(ref);changed.append(ref.blob_id)
 return changed

def garbage_collect(db,actor='system'):
 """回收零引用且无活动台账的归档文件；保留行（purged 墓碑）供审计与同内容复用。"""
 removed=[];protected=[]
 for blob in db.query(ArchiveBlob).all():
  active=db.query(ArchiveRef).filter_by(blob_id=blob.id).count()
  if active>0 or (blob.ref_count or 0)>0:
   protected.append(blob.checksum);continue
  path=archive_store.get_path(blob.checksum)
  if blob.status=='purged': continue
  if not path.exists():
   blob.status='corrupted';blob.corrupt_reason='回收时发现文件缺失';continue
  # 删除是最后一步：先确认无任何活动引用，再移除不可变文件
  path.unlink()
  blob.status='purged';blob.corrupt_reason='';blob.last_verified=time.time()
  removed.append(blob.checksum)
 from src.services.audit import log
 log(db,actor,'archive.gc',{'removed':len(removed),'protected':len(protected)})
 db.commit()
 return {'removed':removed,'protected':len(protected)}

def load_run_snapshot(db,run):
 """兼容新旧记录：优先归档，退回内联快照；损坏拒绝读取。"""
 if run.snapshot_blob_id:
  blob=db.get(ArchiveBlob,run.snapshot_blob_id)
  if blob is None: raise HTTPException(409,'归档元数据缺失，报告快照不可读')
  return archive_store.load(db,blob)
 if run.snapshot is None: raise HTTPException(409,'报告快照不存在')
 return run.snapshot

def load_delivery_payload(db,delivery):
 if delivery.payload_blob_id:
  blob=db.get(ArchiveBlob,delivery.payload_blob_id)
  if blob is None: raise HTTPException(409,'归档元数据缺失，交付内容不可读')
  return archive_store.load(db,blob)
 if delivery.payload is None: raise HTTPException(409,'交付内容不存在')
 return delivery.payload

def hydrate_run(db,run):
 """列表用：装载快照；损坏时打标记而不是让整页失败。"""
 data=dict((c.name,getattr(run,c.name)) for c in run.__table__.columns)
 try: data['snapshot']=load_run_snapshot(db,run);data['archive_corrupt']=False
 except HTTPException as exc:
  data['snapshot']=None;data['archive_corrupt']=True;data['archive_error']=exc.detail
 return data

def hydrate_delivery(db,delivery):
 data=dict((c.name,getattr(delivery,c.name)) for c in delivery.__table__.columns)
 try: data['payload']=load_delivery_payload(db,delivery);data['archive_corrupt']=False
 except HTTPException as exc:
  data['payload']=None;data['archive_corrupt']=True;data['archive_error']=exc.detail
 return data
