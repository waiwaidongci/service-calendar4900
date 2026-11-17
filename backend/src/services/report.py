import hashlib,json,time
from fastapi import HTTPException
from src.models.source import Source,Policy
from src.models.run import Run
from src.services.audit import log
from src.services import archive_store,archive
from src.config.settings import REDIS_URL
import redis
def submit(db,user,body):
 policy=db.get(Policy,body.policy_id)
 if not policy: raise HTTPException(404,'口径版本不存在')
 sources=[x.data for x in db.query(Source).order_by(Source.id).all() if body.period=='全部' or x.data.get('period')==body.period]
 if not sources: raise HTTPException(422,'所选期间没有底稿')
 snapshot={'policy':policy.data,'sources':sources,'period':body.period}
 # 业务校验和（与历史报告编号、数值展示保持同一口径）
 checksum=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 # 冻结底稿与口径写入内容寻址不可变归档：相同快照复用同一 blob，只存一份
 blob=archive_store.put(db,snapshot,'run_snapshot')
 obj=Run(name=body.name,status='queued',snapshot=None,checksum=checksum,created=time.time(),approved=False,snapshot_blob_id=blob.id)
 db.add(obj);db.flush()
 archive.add_ref(db,blob,'sla_runs',obj.id,'run_snapshot')
 log(db,user.email,'run.queue',body.name);db.commit()
 redis.Redis.from_url(REDIS_URL).rpush('report-jobs',obj.id)
 return obj
def approve(db,user,id,note):
 obj=db.get(Run,id)
 if not obj: raise HTTPException(404,'报告不存在')
 if obj.status!='completed': raise HTTPException(409,'仅可复核已完成报告')
 if obj.approved: raise HTTPException(409,'报告已经复核')
 obj.approved=True;obj.review=note;log(db,user.email,'run.approve',id);db.commit();return obj
