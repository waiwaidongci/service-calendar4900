from fastapi import APIRouter,Depends,Query
from src.config.database import database
from src.controllers.auth import current,admin
from src.models.archive import ArchiveBlob,ArchiveMigration,ArchiveRef,ArchiveTombstone
from src.services import migration,archive,purge
from src.services.archive_store import verify_all
router=APIRouter()

@router.get('/archive/overview')
def overview(db=Depends(database),u=Depends(current)):
 return purge.storage_overview(db)

@router.get('/archive/blobs')
def blobs(db=Depends(database),u=Depends(current)):
 out=[]
 for b in db.query(ArchiveBlob).order_by(ArchiveBlob.id.desc()).all():
  out.append({'id':b.id,'kind':b.kind,'checksum':b.checksum,'size':b.size,'ref_count':b.ref_count,
   'status':b.status,'corrupt_reason':b.corrupt_reason,'created':b.created,'last_verified':b.last_verified})
 return out

@router.get('/archive/refs')
def refs(db=Depends(database),u=Depends(admin)):
 out=[{'id':r.id,'blob_id':r.blob_id,'ref_table':r.ref_table,'ref_id':r.ref_id,'kind':r.kind,
  'released':False,'released_at':None,'created':r.created}
  for r in db.query(ArchiveRef).order_by(ArchiveRef.id.desc()).limit(300).all()]
 out += [{'id':t.id,'blob_id':t.blob_id,'ref_table':t.ref_table,'ref_id':t.ref_id,'kind':t.kind,
  'released':True,'released_at':t.released_at,'created':t.created}
  for t in db.query(ArchiveTombstone).order_by(ArchiveTombstone.id.desc()).limit(300).all()]
 return out

@router.get('/archive/corrupt')
def corrupt(db=Depends(database),u=Depends(current)):
 return [{'id':b.id,'kind':b.kind,'checksum':b.checksum,'status':b.status,'reason':b.corrupt_reason,
   'last_verified':b.last_verified} for b in db.query(ArchiveBlob).filter(ArchiveBlob.status=='corrupted').all()]

@router.post('/archive/migrate')
def start(scope:str=Query('all'),db=Depends(database),u=Depends(admin)):
 if scope not in ('all','runs','deliveries'): scope='all'
 m=migration.start(db,u,scope)
 return migration.progress(db,m.id)

@router.get('/archive/migrations')
def migrations(db=Depends(database),u=Depends(current)):
 return [migration.progress(db,m.id) for m in db.query(ArchiveMigration).order_by(ArchiveMigration.id.desc()).limit(20).all()]

@router.get('/archive/migrations/{mid}')
def one(mid:int,db=Depends(database),u=Depends(current)):
 p=migration.progress(db,mid)
 if not p:from fastapi import HTTPException;raise HTTPException(404,'迁移任务不存在')
 return p

@router.post('/archive/gc')
def gc(db=Depends(database),u=Depends(admin)):
 return archive.garbage_collect(db,u.email)

@router.post('/archive/verify')
def verify(db=Depends(database),u=Depends(admin)):
 bad=verify_all(db)
 return {'checked_corrupt':[{'id':b.id,'checksum':b.checksum,'reason':b.corrupt_reason} for b in bad]}
