import time
from fastapi import HTTPException
from src.models.run import Run,Schedule,Delivery
from src.services.audit import log
from src.services import archive_store,archive

def add_schedule(db,user,body):
 run=db.get(Run,body.run_id)
 if not run or not run.approved: raise HTTPException(409,'定时交付需引用已复核报告')
 obj=Schedule(name=body.name,run_id=run.id,interval_seconds=body.interval_seconds,next_at=time.time()+body.interval_seconds,enabled=True)
 db.add(obj);log(db,user.email,'schedule.create',body.name);db.commit();db.refresh(obj);return obj

def tick(db):
 now=time.time()
 for schedule in db.query(Schedule).filter(Schedule.enabled==True,Schedule.next_at<=now).all():
  run=db.get(Run,schedule.run_id)
  if run and run.approved and run.status=='completed':
   key=f'{schedule.id}:{schedule.next_at}'
   if not db.query(Delivery).filter_by(due_key=key).first():
    # 交付内容引用报告归档快照；损坏时本次不产出残缺交付，留待下轮重试
    snapshot=archive.load_run_snapshot(db,run)
    payload={'name':run.name,'checksum':run.checksum,'result':run.result,'review':run.review,'policy':snapshot['policy']}
    blob=archive_store.put(db,payload,'delivery_payload')
    obj=Delivery(schedule_id=schedule.id,run_id=run.id,due_key=key,created=now,payload=None,payload_blob_id=blob.id)
    db.add(obj);db.flush()
    archive.add_ref(db,blob,'deliveries',obj.id,'delivery_payload')
    log(db,'worker','delivery.created',key)
  schedule.next_at=now+schedule.interval_seconds
 db.commit()
