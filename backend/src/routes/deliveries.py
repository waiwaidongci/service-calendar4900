from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import Response
from src.config.database import database
from src.controllers.auth import current,admin
from src.models.run import Schedule,Delivery
from src.schemas.requests import ScheduleInput
from src.services.delivery import add_schedule
from src.services.export import csv_text
from src.services import archive,purge
router=APIRouter()
@router.get('/schedules')
def schedules(db=Depends(database),u=Depends(current)): return [{c.name:getattr(x,c.name) for c in x.__table__.columns} for x in db.query(Schedule).all()]
@router.post('/schedules')
def create(b:ScheduleInput,db=Depends(database),u=Depends(admin)):
 obj=add_schedule(db,u,b);return {c.name:getattr(obj,c.name) for c in obj.__table__.columns}
@router.post('/schedules/{id}/toggle')
def toggle(id:int,db=Depends(database),u=Depends(admin)):
 obj=db.get(Schedule,id)
 if not obj: raise HTTPException(404,'计划不存在')
 obj.enabled=not obj.enabled;db.commit();return {c.name:getattr(obj,c.name) for c in obj.__table__.columns}
@router.get('/deliveries')
def deliveries(db=Depends(database),u=Depends(current)):
 return [archive.hydrate_delivery(db,x) for x in db.query(Delivery).order_by(Delivery.id.desc()).limit(100).all()]
@router.delete('/deliveries/{id}')
def remove(id:int,db=Depends(database),u=Depends(admin)): return purge.purge_delivery(db,u,id)
@router.get('/deliveries/{id}/csv')
def download(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(Delivery,id)
 if not obj: raise HTTPException(404,'交付不存在')
 payload=archive.load_delivery_payload(db,obj)  # 损坏在此抛出 409，绝不返回残缺 CSV
 return Response(csv_text(payload['result']),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="delivery-{id}.csv"'})
