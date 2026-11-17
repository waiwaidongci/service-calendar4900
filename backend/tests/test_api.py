import json,hashlib,time
from src.services import migration,archive_store,archive
from src.models.run import Run,Delivery
from src.models.archive import ArchiveBlob

def legacy_run(db,seeded):
 snapshot={'policy':seeded['policy'],'sources':seeded['rows'],'period':'全部'}
 checksum=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 r=Run(name='legacy',status='completed',snapshot=snapshot,checksum=checksum,created=time.time(),approved=True,review='已核对',result={'rows':[{'x':1}]})
 db.add(r);db.commit();return r

def test_full_flow_new_and_legacy_records_readable(client,db,seeded):
 from conftest import login,drain_jobs
 admin=login(client,'admin');viewer=login(client,'viewer');analyst=login(client,'analyst')
 # 一条旧式内联记录
 old=legacy_run(db,seeded)
 # 一条新报告（直接归档）
 r=client.post('/runs',headers=admin,json={'name':'新核算','policy_id':1,'period':'全部'})
 assert r.status_code==200,r.text
 new_id=r.json()['id']
 drain_jobs()  # worker 完成新报告核算
 # 新旧记录列表与详情均可读，结构一致
 listing=client.get('/runs',headers=admin).json()
 assert len(listing)==2
 for item in listing:
  assert item['snapshot'] is not None and item['archive_corrupt'] is False
 assert client.get('/runs/%d'%old.id,headers=viewer).status_code==200
 # 启动并完成迁移
 m=client.post('/archive/migrate?scope=all',headers=admin).json()
 assert m['total']>=1
 drain_jobs()  # worker 执行迁移
 prog=client.get('/archive/migrations/%d'%m['id'],headers=admin).json()
 assert prog['status']=='done' and prog['failed']==0
 # 迁移后旧记录数值/编号不变且仍可读
 detail=client.get('/runs/%d'%old.id,headers=admin).json()
 assert detail['id']==old.id and detail['checksum']==old.checksum
 assert detail['snapshot']['sources']==seeded['rows']
 # 相同内容只存一份 blob
 assert db.query(ArchiveBlob).filter_by(kind='run_snapshot').count()==1
 # viewer 可下载已复核 CSV（迁移后下载内容不变）
 csv=client.get('/runs/%d/csv'%old.id,headers=viewer)
 assert csv.status_code==200 and 'text/csv' in csv.headers['content-type']
 assert '﻿x' in csv.text
 # 权限：viewer/analyst 不能启动迁移、GC、清退
 for h in (viewer,analyst):
  assert client.post('/archive/migrate',headers=h).status_code==403
  assert client.post('/archive/gc',headers=h).status_code==403
  assert client.delete('/runs/%d'%new_id,headers=h).status_code==403

def test_purge_run_and_delivery_with_gc(client,db,seeded):
 from conftest import login
 admin=login(client,'admin')
 r=client.post('/runs',headers=admin,json={'name':'待清退','policy_id':1,'period':'全部'}).json()
 blob_id=r['snapshot_blob_id']
 res=client.delete('/runs/%d'%r['id'],headers=admin)
 assert res.status_code==200
 blob=db.get(ArchiveBlob,blob_id)
 assert blob.status=='purged'
 assert client.get('/runs/%d'%r['id'],headers=admin).status_code==404
 # 收件箱清退
 old=legacy_run(db,seeded)
 d=Delivery(schedule_id=0,run_id=old.id,due_key='dk%d'%time.time_ns(),created=time.time(),
            payload={'name':'d','result':{'rows':[{'a':1}]},'policy':seeded['policy'],'checksum':'x','review':'r'})
 db.add(d);db.commit()
 rd=client.delete('/deliveries/%d'%d.id,headers=admin)
 assert rd.status_code==200 and client.get('/deliveries',headers=admin).status_code==200

def test_corruption_visible_and_blocks_download(client,db,seeded):
 from conftest import login
 admin=login(client,'admin');viewer=login(client,'viewer')
 r=client.post('/runs',headers=admin,json={'name':'损坏用','policy_id':1,'period':'全部'}).json()
 blob=db.get(ArchiveBlob,r['snapshot_blob_id'])
 archive_store.get_path(blob.checksum).write_bytes(b'xxx-not-json')
 # 列表不整页崩，带损坏标记；损坏原因可在管理端查看
 listing=client.get('/runs',headers=admin).json()
 me=next(x for x in listing if x['id']==r['id'])
 assert me['archive_corrupt'] is True
 corrupt=client.get('/archive/corrupt',headers=admin).json()
 assert any(x['checksum']==blob.checksum and x['reason'] for x in corrupt)
 # 交付侧：手工构造引用损坏 blob 的收件箱记录，下载必须 409
 d=Delivery(schedule_id=0,run_id=r['id'],due_key='bad%d'%time.time_ns(),created=time.time(),
            payload=None,payload_blob_id=blob.id)
 db.add(d);db.commit()
 assert client.get('/deliveries/%d/csv'%d.id,headers=viewer).status_code==409
 dl=client.get('/deliveries',headers=admin).json()
 item=next(x for x in dl if x['id']==d.id)
 assert item['archive_corrupt'] is True

def test_migration_progress_endpoint_and_overview(client,db,seeded):
 from conftest import login,drain_jobs
 admin=login(client,'admin')
 legacy_run(db,seeded)
 client.post('/archive/migrate',headers=admin)
 drain_jobs()
 migs=client.get('/archive/migrations',headers=admin).json()
 assert migs and migs[0]['status']=='done'
 ov=client.get('/archive/overview',headers=admin).json()
 assert ov['blob_total']>=1 and ov['ok']>=1
 blobs=client.get('/archive/blobs',headers=admin).json()
 assert all('checksum' in b and 'status' in b for b in blobs)
