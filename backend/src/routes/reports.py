from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import Response
from src.config.database import database
from src.controllers.auth import current,editor,admin
from src.models.run import Run
from src.schemas.requests import RunInput,ReviewInput
from src.services.report import submit,approve
from src.services.export import csv_text
from src.services import archive,purge
router=APIRouter()
@router.get('/runs')
def runs(db=Depends(database),u=Depends(current)):
 return [archive.hydrate_run(db,x) for x in db.query(Run).order_by(Run.id.desc()).all()]
@router.post('/runs')
def create(b:RunInput,db=Depends(database),u=Depends(editor)): return archive.hydrate_run(db,submit(db,u,b))
@router.get('/runs/{id}')
def detail(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(Run,id)
 if not obj: raise HTTPException(404,'报告不存在')
 return archive.hydrate_run(db,obj)
@router.post('/runs/{id}/approve')
def review(id:int,b:ReviewInput,db=Depends(database),u=Depends(admin)): return archive.hydrate_run(db,approve(db,u,id,b.note))
@router.delete('/runs/{id}')
def remove(id:int,db=Depends(database),u=Depends(admin)): return purge.purge_run(db,u,id)
@router.get('/runs/{id}/csv')
def download(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(Run,id)
 if not obj or obj.status!='completed': raise HTTPException(409,'报告尚未完成')
 if u.role=='viewer' and not obj.approved: raise HTTPException(403,'业务阅读者仅可下载已复核报告')
 result=obj.result
 if not result:
  # 结果缺失而快照在归档中的损坏/缺失情形：拒绝给出残缺下载
  snapshot=archive.load_run_snapshot(db,obj)
  raise HTTPException(409,'报告结果不完整，拒绝下载残缺文件')
 return Response(csv_text(result),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="report-{id}.csv"'})
