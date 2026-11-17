import json,hashlib,time,threading
from src.services import migration,archive,archive_store,purge
from src.models.run import Run,Delivery,Schedule
from src.models.archive import ArchiveBlob,ArchiveRef,ArchiveTombstone,ArchiveMigration,MigrationItem
from src.services.delivery import tick

class U:
 def __init__(self,email): self.email=email;self.role='admin'
ADMIN=U('admin@demo.local')

def legacy_run(db,seeded,idx,completed=True):
 snapshot={'policy':seeded['policy'],'sources':seeded['rows'],'period':'全部'}
 checksum=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 r=Run(name='legacy-%d'%idx,status='completed' if completed else 'queued',snapshot=snapshot,
       checksum=checksum,created=time.time(),approved=True,review='ok')
 db.add(r);db.commit();return r

def legacy_delivery(db,run):
 payload={'name':run.name,'checksum':run.checksum,'result':run.result or {'rows':[{'a':1}]},'review':run.review,'policy':run.snapshot['policy']}
 d=Delivery(schedule_id=0,run_id=run.id,due_key='k-%d-%d'%(run.id,time.time_ns()),created=time.time(),payload=payload)
 db.add(d);db.commit();return d

def test_migration_idempotent_and_dedupes(db,seeded):
 runs=[legacy_run(db,seeded,i) for i in range(3)]
 m=migration.start(db,ADMIN)
 assert m.total==3
 migration.run_migration(db,db.get(ArchiveMigration,m.id),'w1')
 p=migration.progress(db,m.id)
 assert p['status']=='done' and p['migrated']==3 and p['failed']==0
 # 全部转入归档，内联副本已释放，且同内容只存一份
 blobs=db.query(ArchiveBlob).count()
 assert blobs==1
 assert all(db.get(Run,r.id).snapshot is None and db.get(Run,r.id).snapshot_blob_id for r in runs)
 # 中断续跑/重试：再次启动不应重复归档或重复引用
 m2=migration.start(db,ADMIN)
 migration.run_migration(db,db.get(ArchiveMigration,m2.id),'w1')
 p2=migration.progress(db,m2.id)
 assert db.query(ArchiveBlob).count()==1
 assert db.query(ArchiveRef).count()==3
 # 迁移后数值/读取与原内联完全一致
 for r in runs:
  snap=archive.load_run_snapshot(db,db.get(Run,r.id))
  assert snap['sources']==seeded['rows'] and snap['policy']==seeded['policy']

def test_migration_resumes_after_interruption(db,seeded):
 runs=[legacy_run(db,seeded,i) for i in range(4)]
 m=migration.start(db,ADMIN)
 # 只处理前两条，模拟 worker 中断
 sess=migration
 items=db.query(MigrationItem).filter_by(state='pending').order_by(MigrationItem.id).limit(2).all()
 for it in items:
  migration._migrate_one(db,it);db.commit()
 partial=db.get(ArchiveMigration,m.id)
 partial.migrated=2;db.commit()
 assert db.query(MigrationItem).filter(MigrationItem.state.in_(['pending','running'])).count()==2
 # 新 worker 接管续跑，不漏记录
 fresh=db.get(ArchiveMigration,m.id)
 migration.run_migration(db,fresh,'w2')
 p=migration.progress(db,m.id)
 assert p['migrated']>=4 and p['failed']==0
 assert db.query(MigrationItem).filter(MigrationItem.state.in_(['pending','running'])).count()==0
 assert all(db.get(Run,r.id).snapshot_blob_id for r in runs)

def test_concurrent_migrations_no_dup_no_loss(db,seeded):
 runs=[legacy_run(db,seeded,i) for i in range(6)]
 m=migration.start(db,ADMIN)
 errors=[]
 def worker(name):
  from conftest import TestSession
  try:
   for _ in range(20):
    with TestSession() as s:
     mm=s.get(ArchiveMigration,m.id)
     if s.query(MigrationItem).filter_by(migration_id=m.id).filter(MigrationItem.state.in_(['pending','running'])).count()==0: break
     migration.run_migration(s,mm,name)
  except Exception as e: errors.append((name,repr(e)))
 ts=[threading.Thread(target=worker,args=('w%d'%i,)) for i in range(3)]
 for t in ts:t.start()
 for t in ts:t.join()
 assert not errors,errors
 assert db.query(MigrationItem).filter(MigrationItem.state.in_(['pending','running'])).count()==0
 assert db.query(ArchiveBlob).count()==1                     # 不重复归档
 assert db.query(ArchiveRef).count()==len(runs)
 from conftest import TestSession
 with TestSession() as verify:
  for r in runs:
   fresh=verify.get(Run,r.id)
   assert fresh.snapshot_blob_id is not None and fresh.snapshot is None

def test_migration_skips_purged_records_via_tombstone(db,seeded):
 r1=legacy_run(db,seeded,1);r2=legacy_run(db,seeded,2)
 # 先制造 r1 的归档与墓碑（模拟已清退）
 blob=archive_store.put(db,r1.snapshot,'run_snapshot')
 archive.add_ref(db,blob,'sla_runs',r1.id,'run_snapshot');db.commit()
 purge.purge_run(db,ADMIN,r1.id)                            # 业务记录删除，墓碑留存
 m=migration.start(db,ADMIN)
 # r1 已不在库（spec 构建时自然排除）；r2 正常迁移
 migration.run_migration(db,db.get(ArchiveMigration,m.id),'w1')
 p=migration.progress(db,m.id)
 assert p['failed']==0
 assert db.get(Run,r2.id).snapshot_blob_id is not None
 # 墓碑仍在且未被任何迟到迁移复活
 tomb=db.query(ArchiveTombstone).filter_by(ref_table='sla_runs',ref_id=r1.id).first()
 assert tomb is not None
 assert db.query(ArchiveRef).filter_by(ref_table='sla_runs',ref_id=r1.id).count()==0

def test_migration_covers_deliveries_and_keeps_downloads(db,seeded):
 r=legacy_run(db,seeded,1);d=legacy_delivery(db,r)
 expected=d.payload
 m=migration.start(db,ADMIN,scope='all')
 migration.run_migration(db,db.get(ArchiveMigration,m.id),'w1')
 d2=db.get(Delivery,d.id)
 assert d2.payload is None and d2.payload_blob_id is not None
 payload=archive.load_delivery_payload(db,d2)
 assert payload['name']==r.name
 assert payload['result']==expected['result']

def test_stale_sweep_claims_heartbeat_timeout(db,seeded):
 runs=[legacy_run(db,seeded,i) for i in range(2)]
 m=migration.start(db,ADMIN)
 mm=db.get(ArchiveMigration,m.id);mm.status='running';mm.worker='dead';mm.heartbeat=time.time()-999;db.commit()
 migration.sweep_stale(db,'new-worker')
 p=migration.progress(db,m.id)
 assert p['status']=='done' and p['worker']=='new-worker'
