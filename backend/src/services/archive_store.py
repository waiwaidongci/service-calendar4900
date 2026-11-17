"""内容寻址不可变归档存储。

- blob 以规范化 JSON 的 sha256 为键，相同内容全局仅一份
- 原子写入（同目录临时文件 + rename），目录两级分片
- 读取时强制校验大小与 sha256，损坏即抛 ArchiveCorrupt 并登记原因
"""
import hashlib,json,os,tempfile,time
from pathlib import Path
from fastapi import HTTPException
from src.config.settings import ARCHIVE_DIR
from src.models.archive import ArchiveBlob

class ArchiveCorrupt(HTTPException):
 def __init__(self,checksum,reason):
  super().__init__(409,'归档已损坏，拒绝读取或下载：'+reason)
  self.checksum=checksum;self.reason=reason

def canonical_bytes(data):
 return json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')

def content_checksum(data):
 return hashlib.sha256(canonical_bytes(data)).hexdigest()

def _path(checksum):
 root=Path(ARCHIVE_DIR)
 return root/checksum[:2]/checksum[2:4]/(checksum+'.json')

def _write_file(checksum,raw):
 path=_path(checksum);path.parent.mkdir(parents=True,exist_ok=True)
 # 同文件系统临时文件，rename 为原子操作；并发写同一 checksum 以已落盘内容为准
 fd,tmp=tempfile.mkstemp(prefix='.tmp-'+checksum[:12]+'-',dir=str(path.parent))
 try:
  with os.fdopen(fd,'wb') as f:
   f.write(raw);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp): os.unlink(tmp)

def _mark_corrupt(db,blob,reason):
 blob.status='corrupted';blob.corrupt_reason=reason;blob.last_verified=time.time()
 db.commit()

def put(db,data,kind):
 """写入或复用 blob，返回 ArchiveBlob（未提交，由调用方控制事务）。"""
 raw=canonical_bytes(data);checksum=hashlib.sha256(raw).hexdigest();now=time.time()
 blob=db.query(ArchiveBlob).filter_by(checksum=checksum).first()
 if blob is None:
  _write_file(checksum,raw)
  blob=ArchiveBlob(kind=kind,checksum=checksum,size=len(raw),ref_count=0,status='ok',created=now,last_verified=now)
  db.add(blob);db.flush()
  return blob
 if blob.status=='purged' or not _path(checksum).exists():
  # 同内容重新落盘（清退后被新报告复用 / 文件缺失修复）
  _write_file(checksum,raw)
  blob.status='ok';blob.corrupt_reason='';blob.size=len(raw);blob.kind=kind;blob.last_verified=now
 elif blob.status=='corrupted':
  # 以本次内存中的完好内容重写，修复可验证的损坏
  _write_file(checksum,raw)
  blob.status='ok';blob.corrupt_reason='';blob.size=len(raw);blob.last_verified=now
 else:
  blob.last_verified=now
 return blob

def get_path(checksum):
 return _path(checksum)

def load(db,blob):
 """读取并完整校验 blob；损坏时登记原因并拒绝服务。"""
 path=_path(blob.checksum)
 reason=''
 if not path.exists(): reason='归档文件缺失'
 else:
  try:
   raw=path.read_bytes()
   if len(raw)!=blob.size and blob.size: reason='大小不符（预期%s/实际%s）'%(blob.size,len(raw))
   elif hashlib.sha256(raw).hexdigest()!=blob.checksum: reason='sha256校验失败，内容已被篡改或截断'
  except OSError as exc:
   reason='读取失败：'+str(exc)
 if reason:
  _mark_corrupt(db,blob,reason)
  raise ArchiveCorrupt(blob.checksum,reason)
 try:
  return json.loads(raw)
 except Exception as exc:
  reason='JSON解析失败：'+str(exc);_mark_corrupt(db,blob,reason)
  raise ArchiveCorrupt(blob.checksum,reason)

def verify_all(db):
 """后台/手工巡检：逐个校验全部 ok blob，返回损坏清单。"""
 bad=[]
 for blob in db.query(ArchiveBlob).filter(ArchiveBlob.status!='purged').all():
  path=_path(blob.checksum)
  if not path.exists(): _mark_corrupt(db,blob,'归档文件缺失');bad.append(blob);continue
  try:
   raw=path.read_bytes();ok=(not blob.size or len(raw)==blob.size) and hashlib.sha256(raw).hexdigest()==blob.checksum
   if ok: blob.last_verified=time.time();db.commit()
   else: _mark_corrupt(db,blob,'巡检校验失败');bad.append(blob)
  except OSError as exc:
   _mark_corrupt(db,blob,'巡检读取失败：'+str(exc));bad.append(blob)
 return bad
