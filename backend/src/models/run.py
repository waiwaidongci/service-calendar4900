from sqlalchemy import Column,Integer,String,JSON,Text,Float,Boolean
from src.config.database import Base
class Run(Base):
 __tablename__='sla_runs'
 id=Column(Integer,primary_key=True); name=Column(String(120)); status=Column(String(20)); snapshot=Column(JSON); checksum=Column(String(64)); result=Column(JSON); error=Column(Text); created=Column(Float); approved=Column(Boolean,default=False); review=Column(Text,default='')
 # 归档迁移后 snapshot 置空，改由内容寻址 blob 承载；两者并存期旧记录照常读取
 snapshot_blob_id=Column(Integer,nullable=True)
class Schedule(Base):
 __tablename__='delivery_schedules'
 id=Column(Integer,primary_key=True); name=Column(String(120)); run_id=Column(Integer); interval_seconds=Column(Integer); next_at=Column(Float); enabled=Column(Boolean,default=True)
class Delivery(Base):
 __tablename__='deliveries'
 id=Column(Integer,primary_key=True); schedule_id=Column(Integer); run_id=Column(Integer); due_key=Column(String(120),unique=True); created=Column(Float); payload=Column(JSON)
 payload_blob_id=Column(Integer,nullable=True)
