import {useState} from 'react';import {sensitivityStore} from '../stores/sensitivity';import {itemStatus,diagnosticStatus} from '../constants/enums';import type {SensitivityDiagnostic,SensitivityItem} from '../types/domain';
function metricOrDash(value:number|null|undefined,format:(n:number)=>string){return value==null?'—':format(value)}
function ItemDetail({item}:{item:SensitivityItem}){
  const m=item.metrics;
  return <div className="sensitivity-detail"><dl>
    <div><dt>剔除后剩余</dt><dd>{m.remaining} 份 / 有效秩 {m.active_rank??'—'}</dd></div>
    <div><dt>均值偏移</dt><dd>{metricOrDash(m.mean_shift,v=>v.toExponential(4))}（相对基线 {metricOrDash(m.mean_shift_relative,v=>(v*100).toFixed(3)+'%')}）</dd></div>
    <div><dt>GPA均值偏移</dt><dd>{metricOrDash(m.gpa_mean_shift,v=>v.toExponential(4))}</dd></div>
    <div><dt>累计解释率（保留{m.effective_retained}）</dt><dd>{metricOrDash(m.cumulative_explained,v=>(v*100).toFixed(4)+'%')} / 变化 {metricOrDash(m.cumulative_explained_delta,v=>(v>=0?'+':'')+(v*100).toFixed(4)+'pp')}</dd></div>
    <div><dt>最大/平均主子空间夹角</dt><dd>{metricOrDash(m.angle_max_deg,v=>v.toFixed(4)+'°')} / {metricOrDash(m.angle_mean_deg,v=>v.toFixed(4)+'°')}</dd></div>
    <div><dt>子空间可判定</dt><dd>{m.subspace_decidable===true?'是（保留边界不在近重根簇）':m.subspace_decidable===false?'否（保留边界落在近重根簇）':'未比较'}</dd></div>
    {m.repeat_cluster&&<div><dt>剔除后近重根簇</dt><dd>{m.repeat_cluster.map(v=>v.toExponential(3)).join(' / ')}</dd></div>}
    <div><dt>收敛 / 迭代 / 最小det(R)</dt><dd>{m.converged==null?'—':(m.converged?'是':'否')} / {m.iterations??'—'}步 / {metricOrDash(m.determinant_min,v=>v.toFixed(9))}</dd></div>
    <div><dt>影响值（{m.influence_partial?'部分比较':'三项可比'}）</dt><dd>{metricOrDash(m.influence,v=>v.toFixed(6))}；来源 {m.influence_basis||'—'}</dd></div>
  </dl>{m.principal_angles_deg&&<pre>主子空间夹角（度） {JSON.stringify(m.principal_angles_deg.map(v=>Number(v.toFixed(6))))}</pre>}<details><summary>原始度量 JSON</summary><pre>{JSON.stringify(m,null,2)}</pre></details></div>;
}
export default function SensitivityPanel({runId,diagnostic,busy,canRequest,onRequest}:{runId:number;diagnostic:SensitivityDiagnostic|null;busy:boolean;canRequest:boolean;onRequest:()=>void}){
  const {byRun,error:storeError}=sensitivityStore.use(),[openId,setOpenId]=useState(0);
  // The page owns fetched state; the store lets polling updates propagate.
  const view=diagnostic||byRun[runId]?.diagnostic||null;
  const requestError=byRun[runId]===undefined?storeError:'';
  if(!view)return <section className="sensitivity-panel" aria-label="逐片剔除敏感性诊断"><header className="sensitivity-head"><div><p className="eyebrow">LEAVE-ONE-OUT SENSITIVITY</p><h2>逐片剔除敏感性诊断</h2></div><button className="primary" disabled={busy||!canRequest} onClick={onRequest}>{busy?'请求中…':'建立诊断 →'}</button></header><p className="muted">每次剔除一片裁片，用本研究冻结的输入重新配准与拟合，比较均值偏移、累计解释率与保留子空间夹角。原研究结果不被覆盖；重复执行复用已算裁片，不新增结果。</p>{requestError&&<p className="error-text" role="alert">{requestError}</p>}</section>;
  const summary=view.summary||{},baseline=summary.baseline,clusters=summary.near_repeat?.clusters||[],counts=summary.counts||{};
  const pending=view.status==='queued'||view.status==='running';
  const openItem=view.items.find(item=>item.id===openId);
  return <section className="sensitivity-panel" aria-label="逐片剔除敏感性诊断">
    <header className="sensitivity-head"><div><p className="eyebrow">LEAVE-ONE-OUT SENSITIVITY</p><h2>逐片剔除敏感性诊断</h2></div><div className="sensitivity-head-actions"><span className={'status '+view.status}>{diagnosticStatus[view.status]||view.status}</span>{view.status==='failed'&&<button className="primary" disabled={busy||!canRequest} onClick={onRequest}>{busy?'续跑中…':'从中断处续跑 →'}</button>}</div></header>
    <p className="muted">诊断依据是本研究的冻结快照（{summary.basis_source||'run.result.snapshot'}）；之后修改源数据不改变本次诊断。指纹校验{summary.fingerprint_verified===false?'未通过':'通过'}。{summary.published_result_untouched?'原研究结果只读，不被本次诊断覆盖。':''}</p>
    {view.error&&<p className="error-text" role="alert">{view.error}</p>}
    {pending&&<p className="working">后台逐片剔除 {view.total} 份裁片，已完成 {view.items.length} 份；中断后从已算裁片续跑，不重复新增结果。</p>}
    <div className="sensitivity-metrics">
      <div><small>可判定子空间 / 总份数</small><strong>{counts.computed||0} / {summary.item_total??view.total}</strong><span>保留边界不在近重根簇</span></div>
      <div><small>不可判定</small><strong>{counts.undetermined||0}</strong><span>保留边界落在近重根簇</span></div>
      <div><small>样本不足 / 无方差 / 失败</small><strong>{(counts.insufficient||0)+(counts.no_variance||0)+(counts.failed||0)}</strong><span>无法比较保留子空间</span></div>
      <div><small>影响最大</small><strong>{summary.most_influential?summary.most_influential.code:'—'}</strong><span>{summary.most_influential?('影响 '+summary.most_influential.influence.toFixed(3)+(summary.most_influential.partial?'（部分比较）':'')):'尚无可排序裁片'}</span></div>
    </div>
    {baseline&&<p className="muted">{baseline.has_variation?<>基线：有效秩 {baseline.active_rank} / 保留 {view.effective_retained} 模态，累计解释 {baseline.retained_explained_ratio==null?'不报告':(baseline.retained_explained_ratio*100).toFixed(2)+'%'}；保留边界{baseline.boundary_in_repeat?'落在近重根簇，子空间不可判定':'不在近重根簇，按子空间比较'}。</>:<>基线没有可解析的形变方差（有效秩 0，噪声界以对齐数据尺度计），不存在可比保留子空间或累计解释率。</>}排序口径：{summary.ranking_metric}。</p>}
    {clusters.length>0&&<div className="repeat-clusters"><small>近重根簇（相邻相对间隙 ≤ {((summary.near_repeat?.relative_gap_threshold||0)*100).toFixed(0)}%）</small>{clusters.map((c,i)=><span key={i}>PC{c.start}–PC{c.end}：{c.singular_values.map(v=>v.toExponential(3)).join(' / ')}（最大相对间隙 {c.max_relative_gap.toExponential(2)}）</span>)}</div>}
    {view.status==='completed'&&<div className="table-scroll sensitivity-table"><table><thead><tr><th>影响序</th><th>剔除裁片</th><th>状态</th><th>均值偏移 / 相对基线</th><th>累计解释率 / 变化</th><th>最大主子空间夹角</th><th>说明</th></tr></thead><tbody>
      {view.items.map(item=><tr key={item.id} className={openId===item.id?'selected':''}>
        <td>{item.metrics.rank==null?'未排序':'第 '+item.metrics.rank+' 位'}{item.metrics.influence_partial&&item.metrics.rank!=null&&<small>部分比较</small>}</td>
        <td><button className="small-button" onClick={()=>setOpenId(openId===item.id?0:item.id)} aria-expanded={openId===item.id}>{item.code} {openId===item.id?'▾':'▸'}</button><small>{item.name}</small></td>
        <td><span className={'status '+item.status}>{itemStatus[item.status]||item.status}</span></td>
        <td>{metricOrDash(item.metrics.mean_shift,v=>v.toExponential(3))}<small>相对 {metricOrDash(item.metrics.mean_shift_relative,v=>(v*100).toFixed(2)+'%')}</small></td>
        <td>{metricOrDash(item.metrics.cumulative_explained,v=>(v*100).toFixed(2)+'%')}<small>{item.metrics.cumulative_explained_delta==null?'不可比':((item.metrics.cumulative_explained_delta>=0?'+':'')+(item.metrics.cumulative_explained_delta*100).toFixed(2)+'pp')}</small></td>
        <td>{metricOrDash(item.metrics.angle_max_deg,v=>v.toFixed(2)+'°')}<small>{item.metrics.subspace_decidable===false?'子空间不可判定':item.metrics.subspace_decidable===true?'按子空间比较':'未比较'}</small></td>
        <td>{item.reason?<span className="error-text">{item.reason}</span>:<span className="muted">{item.metrics.remaining}份剩余，有效秩{item.metrics.active_rank}</span>}</td>
      </tr>)}
    </tbody></table></div>}
    {view.status==='completed'&&openItem&&<ItemDetail item={openItem}/>}
    {view.status!=='completed'&&!pending&&<p className="muted">诊断未完成，暂无逐片结果。</p>}
    <p className="muted sensitivity-boundary">剔除只重算冻结集合内的其余裁片；不改原研究结果、原始点或模板。子空间夹角按保留k维子空间比较，近重根的单轴方向不具物理唯一性；剩余样本不足或无解析方差时如实标注原因，不用空值冒充可比结果。</p>
  </section>;
}
