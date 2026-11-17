import os,sys,tempfile,types,json,pathlib
import fakeredis

# 隔离环境变量（必须在导入业务代码前设置）
_TMP=tempfile.mkdtemp(prefix='archive-test-')
os.environ['DATABASE_URL']='sqlite:///'+os.path.join(_TMP,'test.db')
os.environ['REDIS_URL']='redis://fake/0'
os.environ['JWT_SECRET']='test-secret'
os.environ['ARCHIVE_DIR']=os.path.join(_TMP,'archive')
os.environ['CONFIG_PATH']=os.path.join(os.path.dirname(__file__),'..','project.json')

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

# fakeredis 替换真实 redis 连接：单例保证应用代码与测试看到同一条队列
import redis as _redis
_FAKE=fakeredis.FakeRedis(decode_responses=False)
def _fake_from_url(url=None,*a,**k): return _FAKE
_redis.Redis.from_url=staticmethod(_fake_from_url)

from src.config import database as db_mod
from src.config.schema_patch import ensure_schema
from src.models.identity import User
from src.models.source import Source,Policy,Period
from src.models.run import Run,Delivery,Schedule
from src.models.archive import ArchiveBlob,ArchiveRef,ArchiveTombstone,ArchiveMigration,MigrationItem
from src.utils.security import password_hash

engine=create_engine(os.environ['DATABASE_URL'],connect_args={'check_same_thread':False,'timeout':5})
from sqlalchemy import event
@event.listens_for(engine,'connect')
def _sqlite_pragma(dbapi_conn,rec):
 cur=dbapi_conn.cursor();cur.execute('PRAGMA busy_timeout=2000');cur.close()
TestSession=sessionmaker(bind=engine,expire_on_commit=False)

@pytest.fixture()
def db():
 db_mod.engine=engine;db_mod.Session=TestSession
 import src.config.schema_patch as sp
 sp.engine=engine
 ensure_schema()
 s=TestSession()
 for M in (MigrationItem,ArchiveMigration,ArchiveTombstone,ArchiveRef,ArchiveBlob,Delivery,Schedule,Run,Source,Policy,Period,User):
  s.query(M).delete()
 s.commit()
 yield s
 s.close()

@pytest.fixture()
def client(db):
 from src.main import app
 with TestClient(app) as c: yield c

def login(client,role):
 r=client.post('/auth/login',json={'email':role+'@demo.local','password':'Demo2026!'})
 assert r.status_code==200,r.text
 return {'Authorization':'Bearer '+r.json()['token']}

def drain_jobs():
 """模拟 worker：排空 Redis 队列中的核算与迁移任务。"""
 from src import worker
 while True:
  item=_FAKE.lpop('report-jobs')
  if not item: break
  worker.handle_report_job(item)
 while True:
  item=_FAKE.lpop('archive-jobs')
  if not item: break
  worker.handle_archive_job(item)

@pytest.fixture()
def seeded(db):
 for role in ('admin','analyst','viewer'):
  if not db.query(User).filter_by(email=role+'@demo.local').first():
   db.add(User(email=role+'@demo.local',role=role,password=password_hash('Demo2026!')))
 policy={'version':'v1','timezone':'Asia/Shanghai','weekdays':[0,1,2,3,4],
  'windows':[[540,720],[780,1080]],'holidays':[],'sla_minutes':{'普通':480,'紧急':120}}
 db.add(Policy(version='v1',data=policy))
 rows=[{'period':'2026-11','department':'建模组','opened':'2026-11-13T10:30:00+08:00','closed':'2026-11-13T16:30:00+08:00','priority':'普通'},
       {'period':'2026-11','department':'采样组','opened':'2026-11-12T09:00:00+08:00','closed':'2026-11-12T12:00:00+08:00','priority':'紧急'}]
 for i,row in enumerate(rows): db.add(Source(natural_key='k%d'%i,data=row))
 db.commit()
 return {'policy':policy,'rows':rows}
