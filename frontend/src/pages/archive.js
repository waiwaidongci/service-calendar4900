import {heading} from '../components/common/header.js';import {table} from '../components/common/table.js';import {esc,time} from '../utils/format.js';
const bytes=n=>(n==null?'-':n>=1048576?(n/1048576).toFixed(2)+' MiB':n>=1024?(n/1024).toFixed(1)+' KiB':n+' B');
const phase={queued:'排队中',running:'迁移中',done:'已完成',failed:'有失败'};
export function archive(c,d){
 const a=d.archive||{};const o=a.overview||{};
 const migRows=(a.migrations||[]).map(m=>({编号:m.id,范围:m.scope,状态:phase[m.status]||m.status,总数:m.total,已迁:m.migrated,跳过:m.skipped,失败:m.failed,执行者:m.worker||'-',更新:m.updated?time(m.updated):'-'}));
 const blobRows=(a.blobs||[]).map(b=>({编号:b.id,类型:b.kind,状态:b.status==='ok'?'完好':b.status==='purged'?'已回收':'损坏',引用:b.ref_count,大小:bytes(b.size),校验和:b.checksum.slice(0,16)+'…',最近校验:b.last_verified?time(b.last_verified):'-'}));
 const badRows=(a.corrupt||[]).map(b=>({编号:b.id,类型:b.kind,校验和:b.checksum,损坏原因:b.reason,发现时间:b.last_verified?time(b.last_verified):'-'}));
 return heading('IMMUTABLE ARCHIVE / 07','不可变归档与迁移','冻结底稿与口径按内容寻址共享存储，相同内容只存一份；此处可发起迁移、查看进度与损坏原因，并在引用解除后回收。')
 +`<section class="archive-cards"><div><small>归档对象</small><strong>${o.blob_total??0}</strong></div><div><small>完好/损坏/已回收</small><strong>${o.ok??0} / ${o.corrupted??0} / ${o.purged??0}</strong></div><div><small>占用空间</small><strong>${bytes(o.bytes)}</strong></div><div><small>活动引用</small><strong>${o.active_refs??0}</strong></div></section>`
 +`<form id="migration-form" class="inline-form"><label>迁移范围<select name="scope"><option value="all">全部历史报告与收件箱</option><option value="runs">仅报告快照</option><option value="deliveries">仅收件箱内容</option></select></label><button>启动/续跑迁移</button></form>`
 +`<div class="archive-actions"><button id="verify-archive">巡检校验归档</button><button id="run-gc">回收零引用归档</button></div>`
 +`<h2>迁移进度（中断自动续跑，重试不重复归档）</h2>${table(migRows)}`
 +(a.migrations?.some(m=>m.failures?.length)?`<h3>失败明细</h3>${table(a.migrations.flatMap(m=>m.failures.map(f=>({迁移:m.id,表:f.table,记录:f.record_id,尝试:f.attempts,原因:f.error}))))}`:'')
 +`<h2>损坏归档（读取与下载将被拒绝）</h2>${badRows.length?table(badRows):'<p class="empty">未发现损坏归档。</p>'}`
 +`<h2>归档对象台账</h2>${table(blobRows)}`;
}
