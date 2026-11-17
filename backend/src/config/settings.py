import os,json
from pathlib import Path
CONFIG=json.loads(Path(os.getenv('CONFIG_PATH','/app/project.json')).read_text())
DATABASE_URL=os.environ['DATABASE_URL']
REDIS_URL=os.getenv('REDIS_URL','redis://redis:6379/0')
SECRET=os.environ['JWT_SECRET']
# 共享卷上的不可变归档根目录（backend 与 worker 挂载同一命名卷）
ARCHIVE_DIR=os.getenv('ARCHIVE_DIR','/app/artifacts/archive')
