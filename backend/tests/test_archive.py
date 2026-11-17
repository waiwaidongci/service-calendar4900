import json,os,time
import pytest
from src.services import archive_store,archive,migration,purge
from src.services.archive_store import ArchiveCorrupt
from src.models.run import Run,Delivery
from src.models.archive import ArchiveBlob,ArchiveRef,ArchiveTombstone,ArchiveMigration,MigrationItem
from src.services.report import submit
from src.services.delivery import tick
from src.schemas.requests import RunInput

class U:
 def __init__(self,email): self.email=email;self.role='admin'
ADMIN=U('admin@demo.local')

def make_run(db,seeded,name='r1'):
 body=RunInput(name=name,policy_id=db.query(__import__('src.models.source',fromlist=['Policy']).Policy).first().id,period='全部')
 return submit(db,ADMIN,body)

def test_new_run_uses_archive_and_dedupes(db,seeded):
 r1=make_run(db,seeded,'r1');r2=make_run(db,seeded,'r2')
 assert r1.snapshot is None and r1.snapshot_blob_id is not None
 assert r2.snapshot_blob_id==r1.snapshot_blob_id          # 相同冻结内容只存一份
 blob=db.get(ArchiveBlob,r1.snapshot_blob_id)
 assert blob.ref_count==2                                  # 两处活动引用
 assert os.path.exists(archive_store.get_path(blob.checksum))
 # 装载器返回与历史内联快照结构一致的数据
 snap=archive.load_run_snapshot(db,r1)
 assert set(snap)=={'policy','sources','period'} and len(snap['sources'])==2
 assert r1.checksum == __import__('hashlib').sha256(json.dumps(snap,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def test_gc_protects_referenced_and_reaps_after_release(db,seeded):
 r1=make_run(db,seeded,'r1');r2=make_run(db,seeded,'r2')
 blob_id=r1.snapshot_blob_id
 gc=archive.garbage_collect(db)
 assert gc['removed']==[]                                  # 仍被引用，不得回收
 # 清退一个引用，blob 仍被另一报告引用
 purge.purge_run(db,ADMIN,r1.id)
 blob=db.get(ArchiveBlob,blob_id)
 assert blob.status=='ok' and blob.ref_count==1
 assert db.query(ArchiveTombstone).filter_by(ref_table='sla_runs',ref_id=r1.id).count()==1
 assert db.query(ArchiveRef).count()==1
 purge.purge_run(db,ADMIN,r2.id)
 blob=db.get(ArchiveBlob,blob_id)
 assert blob.status=='purged'                              # 最后一处引用解除后才回收
 assert not os.path.exists(archive_store.get_path(blob.checksum))

def test_gc_interleaved_with_new_report_never_deletes_live(db,seeded):
 r1=make_run(db,seeded,'r1')
 purge.purge_run(db,ADMIN,r1.id)                          # 队列里 blob 已零引用
 blob_id=r1.snapshot_blob_id
 r2=make_run(db,seeded,'r2')                              # 同内容新建：先登记活动引用
 assert r2.snapshot_blob_id==blob_id
 blob=db.get(ArchiveBlob,blob_id)
 assert blob.status=='ok' and blob.ref_count==1
 gc=archive.garbage_collect(db)
 assert blob.checksum not in gc['removed']                # 交错时不误删
 assert os.path.exists(archive_store.get_path(blob.checksum))

def test_late_migration_cannot_restore_released_ref(db,seeded):
 r1=make_run(db,seeded,'r1');blob_id=r1.snapshot_blob_id
 purge.purge_run(db,ADMIN,r1.id)
 # 模拟迟到的旧迁移：同内容重新 put 后尝试 add_ref 同一业务记录
 snapshot={'policy':seeded['policy'],'sources':seeded['rows'],'period':'全部'}
 blob=archive_store.put(db,snapshot,'run_snapshot')
 with pytest.raises(Exception) as ei:
  archive.add_ref(db,blob,'sla_runs',r1.id,'run_snapshot')
 assert '已清退' in ei.value.detail
 db.rollback()
 tomb=db.query(ArchiveTombstone).filter_by(ref_table='sla_runs',ref_id=r1.id).first()
 assert tomb is not None                          # 墓碑保持，引用未恢复
 assert db.query(ArchiveRef).filter_by(ref_table='sla_runs',ref_id=r1.id).count()==0

def test_corrupted_blob_refuses_read_and_download(db,seeded):
 r1=make_run(db,seeded,'r1');blob=db.get(ArchiveBlob,r1.snapshot_blob_id)
 path=archive_store.get_path(blob.checksum)
 path.write_bytes(b'{"broken":')                          # 模拟截断损坏
 with pytest.raises(ArchiveCorrupt):
  archive.load_run_snapshot(db,r1)
 db.refresh(blob)
 assert blob.status=='corrupted'
 assert ('校验' in blob.corrupt_reason) or ('解析' in blob.corrupt_reason) or ('大小' in blob.corrupt_reason)
 # 损坏不出现在可读快照中（列表打标记而非整页失败）
 hyd=archive.hydrate_run(db,r1)
 assert hyd['archive_corrupt'] is True and hyd['snapshot'] is None
 # 损坏文件不允许被 GC 静默删除，保留原因可查
 gc=archive.garbage_collect(db)
 assert blob.checksum not in gc['removed']
 assert db.get(ArchiveBlob,blob.id).status=='corrupted'

def test_verify_repairs_nothing_but_reports(db,seeded):
 r1=make_run(db,seeded,'r1')
 bad=archive_store.verify_all(db)
 assert bad==[]
