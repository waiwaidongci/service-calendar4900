"""已存在数据库的轻量结构演进：只补缺列、补建新表，不改动既有数据。"""
from sqlalchemy import inspect,text
from src.config.database import engine,Base
from src.models import identity,source,run,archive

_PATCHES={
 'sla_runs':[('snapshot_blob_id','INTEGER')],
 'deliveries':[('payload_blob_id','INTEGER')],
}

def ensure_schema():
 Base.metadata.create_all(engine)
 insp=inspect(engine)
 existing={t.lower() for t in insp.get_table_names()}
 with engine.begin() as conn:
  for table,columns in _PATCHES.items():
   if table not in existing: continue
   have={c['name'].lower() for c in insp.get_columns(table)}
   for name,ddl in columns:
    if name not in have:
     conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
