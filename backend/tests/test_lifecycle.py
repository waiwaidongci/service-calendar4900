import os
from src.services import archive_store,archive,purge
from src.models.archive import ArchiveBlob,ArchiveRef,ArchiveTombstone
from tests.test_archive import make_run,ADMIN

def test_reuse_after_purge_and_idempotent_gc(db,seeded):
 r1=make_run(db,seeded,'r1');checksum=db.get(ArchiveBlob,r1.snapshot_blob_id).checksum
 purge.purge_run(db,ADMIN,r1.id)
 blob=db.query(ArchiveBlob).filter_by(checksum=checksum).one()
 assert blob.status=='purged' and not os.path.exists(archive_store.get_path(checksum))
 # 重复 GC 幂等，不报错也不重复计数
 again=archive.garbage_collect(db)
 assert again['removed']==[]
 # 同内容新建报告：blob 行复用，文件重新落盘，新引用正常建立
 r2=make_run(db,seeded,'r2')
 same=db.get(ArchiveBlob,r2.snapshot_blob_id)
 assert same.id==blob.id and same.status=='ok'
 assert os.path.exists(archive_store.get_path(checksum))
 assert db.query(ArchiveRef).filter_by(ref_table='sla_runs',ref_id=r2.id).count()==1
 assert same.ref_count==1

def test_corrupted_blob_is_not_gced_and_repairable_by_good_put(db,seeded):
 r1=make_run(db,seeded,'r1');blob=db.get(ArchiveBlob,r1.snapshot_blob_id)
 archive_store.get_path(blob.checksum).write_bytes(b'garbage')
 with __import__('pytest').raises(archive_store.ArchiveCorrupt):
  archive.load_run_snapshot(db,r1)
 # 损坏对象不参与回收，保留现场与原因
 gc=archive.garbage_collect(db)
 assert blob.checksum not in gc['removed']
 # 用完好的同内容重新写入可自愈
 data={'policy':seeded['policy'],'sources':seeded['rows'],'period':'全部'}
 fixed=archive_store.put(db,data,'run_snapshot')
 assert fixed.id==blob.id and fixed.status=='ok'
 assert archive.load_run_snapshot(db,r1)==data
