"""页面清退：删除报告或收件箱记录并解除其归档引用。

- 先删业务记录、再释放引用台账，最后执行 GC；GC 与新建报告交错时仍靠
  活动引用计数保护，不会误删仍被引用的归档。
- 清退保留引用墓碑，迟到的旧迁移无法恢复。
"""
from fastapi import HTTPException
from src.models.run import Run,Delivery,Schedule
from src.models.archive import ArchiveBlob
from src.services import archive
from src.services.audit import log

def purge_run(db,user,run_id):
 obj=db.get(Run,run_id)
 if not obj: raise HTTPException(404,'报告不存在')
 # 引用该报告的调度计划失去意义，一并清退；已生成的收件箱交付独立保留
 for sch in db.query(Schedule).filter_by(run_id=run_id).all():
  log(db,user.email,'schedule.purge',sch.id);db.delete(sch)
 archive.release_ref(db,'sla_runs',run_id)
 name=obj.name;db.delete(obj)
 log(db,user.email,'run.purge',{'id':run_id,'name':name})
 db.commit()
 gc=archive.garbage_collect(db,user.email)
 return {'deleted':run_id,'gc':gc}

def purge_delivery(db,user,delivery_id):
 obj=db.get(Delivery,delivery_id)
 if not obj: raise HTTPException(404,'交付不存在')
 archive.release_ref(db,'deliveries',delivery_id)
 db.delete(obj);log(db,user.email,'delivery.purge',delivery_id)
 db.commit()
 gc=archive.garbage_collect(db,user.email)
 return {'deleted':delivery_id,'gc':gc}

def storage_overview(db):
 blobs=db.query(ArchiveBlob).all()
 return {
  'blob_total':len(blobs),
  'bytes':sum(b.size or 0 for b in blobs),
  'ok':sum(1 for b in blobs if b.status=='ok'),
  'corrupted':sum(1 for b in blobs if b.status=='corrupted'),
  'purged':sum(1 for b in blobs if b.status=='purged'),
  'active_refs':sum(b.ref_count or 0 for b in blobs),
 }
