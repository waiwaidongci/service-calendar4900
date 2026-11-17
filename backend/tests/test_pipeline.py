import time
from conftest import login,drain_jobs
from src.models.run import Run,Delivery,Schedule
from src.models.archive import ArchiveBlob,ArchiveRef
from src.services import archive,archive_store

def test_worker_delivery_pipeline_uses_archive(client,db,seeded):
 admin=login(client,'admin');viewer=login(client,'viewer')
 r=client.post('/runs',headers=admin,json={'name':'全链路','policy_id':1,'period':'全部'}).json()
 drain_jobs()  # worker 核算
 run=db.get(Run,r['id'])
 assert run.status=='completed' and run.result is not None
 # 复核
 note='已核对数值与口径边界，同意交付。'
 assert client.post('/runs/%d/approve'%r['id'],headers=admin,json={'note':note}).status_code==200
 # 建最短周期计划
 sch=client.post('/schedules',headers=admin,json={'name':'plan','run_id':r['id'],'interval_seconds':5}).json()
 s=db.get(Schedule,sch['id']);s.next_at=time.time()-1;db.commit()
 from src.services.delivery import tick
 from conftest import TestSession
 with TestSession() as sd:
  tick(sd)
 d=sd.query(Delivery).first()
 assert d is not None and d.payload is None and d.payload_blob_id is not None
 blob=sd.get(ArchiveBlob,d.payload_blob_id)
 assert blob.kind=='delivery_payload' and blob.ref_count==1
 assert sd.query(ArchiveRef).filter_by(ref_table='deliveries',ref_id=d.id).count()==1
 # 收件箱列表与下载内容完好
 listing=client.get('/deliveries',headers=viewer).json()
 item=next(x for x in listing if x['id']==d.id)
 assert item['archive_corrupt'] is False and item['payload']['name']=='全链路'
 csv=client.get('/deliveries/%d/csv'%d.id,headers=viewer)
 assert csv.status_code==200 and 'text/csv' in csv.headers['content-type']
 # 下载字节与归档内结果一致
 payload=archive.load_delivery_payload(sd,sd.get(Delivery,d.id))
 from src.services.export import csv_text
 assert csv.content.decode('utf-8')==csv_text(payload['result'])

def test_report_csv_identical_before_and_after_migration(client,db,seeded):
 import json,hashlib
 admin=login(client,'admin')
 snap={'policy':seeded['policy'],'sources':seeded['rows'],'period':'全部'}
 checksum=hashlib.sha256(json.dumps(snap,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 legacy=Run(name='legacy',status='completed',snapshot=snap,checksum=checksum,created=time.time(),
            approved=True,review='ok',result={'rows':[{'月份':'2026-11','样本数':2}]})
 db.add(legacy);db.commit()
 before=client.get('/runs/%d/csv'%legacy.id,headers=admin).content
 client.post('/archive/migrate',headers=admin);drain_jobs()
 after=client.get('/runs/%d/csv'%legacy.id,headers=admin).content
 assert before==after  # 迁移前后下载内容字节一致，编号与数值不变
 assert client.get('/runs/%d'%legacy.id,headers=admin).json()['checksum']==checksum
