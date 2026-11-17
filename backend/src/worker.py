import time,traceback
from pathlib import Path
import redis
from src.config.database import Base,engine,Session
from src.config.settings import REDIS_URL
from src.models import identity,source,run,archive
from src.config.schema_patch import ensure_schema
from src.models.run import Run
from src.models.archive import ArchiveMigration
from src.services.calculation import calculate
from src.services.delivery import tick
from src.services import archive as archive_service
from src.services import migration
ensure_schema()
r=redis.Redis.from_url(REDIS_URL)
WORKER_NAME='worker-%d'%id(r)
def handle_report_job(item):
 with Session() as db:
  job=db.get(Run,int(item))
  if job and job.status=='queued':
   job.status='running';db.commit()
   try:
    snapshot=archive_service.load_run_snapshot(db,job)  # 损坏即中止，不产出残缺结果
    job.result=calculate(snapshot['sources'],snapshot['policy']);job.status='completed'
   except Exception as exc:
    job.status='failed';job.error=str(exc);traceback.print_exc()
   db.commit()
def handle_archive_job(item):
 with Session() as db:
  m=db.get(ArchiveMigration,int(item))
  if m and m.status in ('queued','running'):
   migration.run_migration(db,m,WORKER_NAME)
def main():
 while True:
  Path('/tmp/worker-heartbeat').write_text(str(time.time()))
  try:
   item=r.lpop('report-jobs')
   if item: handle_report_job(item)
   item=r.lpop('archive-jobs')
   if item: handle_archive_job(item)
   with Session() as db:
    migration.sweep_stale(db,WORKER_NAME)   # 中断续跑：接管心跳超时任务
    tick(db)
  except Exception: traceback.print_exc()
  time.sleep(1)
if __name__=='__main__': main()
