"""Leave-one-out sensitivity diagnostics for a frozen alignment study.

Each specimen is removed once, the remaining frozen points are re-aligned with
the same GPA and PCA settings, and the shifted mean, cumulative explained
ratio and retained subspace are compared with the published result. The
published run result is never rewritten; diagnostics live in their own table
and are idempotent per run.
"""
import hashlib,json
from datetime import datetime
import numpy as np
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from redis import Redis
from src.config.settings import REDIS_URL
from src.models import SensitivityDiagnostic,SensitivityItem
from src.services.deformation import fit_alignment
from src.services.run_service import get_run
from src.constants.enums import RunStatus,DiagnosticStatus,SensitivityItemStatus

# Adjacent resolved singular values within 1% relative gap form a near-repeat
# chain; a retained boundary inside such a chain is not physically unique.
NEAR_REPEAT_REL_GAP=1e-2
MIN_REMAINING=3
BASIS_SOURCE='run.result.snapshot（研究创建时冻结；之后修改源数据不改变诊断依据）'
redis=Redis.from_url(REDIS_URL)

def _canonical_fingerprint(snapshot,retained,tolerance):
    specimens=sorted(snapshot['specimens'],key=lambda s:s['code'])
    normalized={**snapshot,'specimens':specimens}
    canonical={'algorithm':'so2-unit-gpa-arithmetic-pca-v1','snapshot':normalized,'retained':retained,'tolerance':tolerance}
    return hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _near_repeat_clusters(singular_values,active_rank,relative_gap=NEAR_REPEAT_REL_GAP):
    """Chain adjacent resolved singular values whose relative gap is <= threshold."""
    if active_rank<=0:return []
    values=[float(v) for v in singular_values[:active_rank]];clusters=[];start=0
    for i in range(len(values)-1):
        denominator=max(values[i],np.finfo(float).eps)
        if abs(values[i]-values[i+1])/denominator>relative_gap:
            if i>start:clusters.append((start,i+1))
            start=i+1
    if len(values)-1>start:clusters.append((start,len(values)))
    result=[]
    for start,end in clusters:
        segment=values[start:end]
        max_gap=max((abs(segment[i]-segment[i+1])/max(segment[i],np.finfo(float).eps) for i in range(len(segment)-1)),default=0.0)
        result.append({'start':start+1,'end':end,'singular_values':segment,'max_relative_gap':max_gap})
    return result

def _boundary_cluster(clusters,effective_retained):
    """Return the near-repeat cluster cut by the retained boundary, if any."""
    for cluster in clusters:
        if cluster['start']<=effective_retained<cluster['end']:return cluster
    return None

def _principal_angles_deg(base_vectors,sub_vectors,k):
    base=np.asarray(base_vectors,dtype=float)[:k].T;sub=np.asarray(sub_vectors,dtype=float)[:k].T
    if base.ndim!=2 or sub.ndim!=2 or base.shape!=sub.shape or base.shape[1]==0:return None
    cosines=np.linalg.svd(base.T@sub,compute_uv=False)
    angles=np.degrees(np.arccos(np.clip(cosines,-1.0,1.0)))
    return {'max_deg':float(np.max(angles)),'mean_deg':float(np.mean(angles)),'angles_deg':angles.tolist()}

def _baseline(run):
    if run.status!=RunStatus.COMPLETED:raise ValueError('原研究已不再是完成状态，诊断依据不可用')
    result=run.result;snapshot=result['snapshot'];pca=result['pca']
    effective=int(pca['retained']);active=int(pca['active_rank'])
    fingerprint=_canonical_fingerprint(snapshot,run.retained,run.tolerance)
    if fingerprint!=result.get('fingerprint'):raise ValueError('冻结快照指纹与发布结果不一致，诊断依据不可信')
    mean=np.asarray(pca['arithmetic_mean'],dtype=float)
    clusters=_near_repeat_clusters(pca['singular_values'],active)
    boundary=_boundary_cluster(clusters,effective) if active else None
    return {
        'snapshot':snapshot,'pca':pca,'effective_retained':effective,'active_rank':active,
        'has_variation':bool(pca.get('has_variation')) and active>0,
        'mean':mean,'gpa_mean':np.asarray(result['gpa_mean'],dtype=float),
        'mean_norm':float(np.linalg.norm(mean)),
        'cumulative_explained':float(pca['retained_explained_ratio']) if active else None,
        'boundary_cluster':boundary,'clusters':clusters,'fingerprint':fingerprint,
        'requested_retained':int(run.retained),
    }

def _min_remaining(effective_retained):return max(MIN_REMAINING,effective_retained+1)

def _fit_item(base,specimen,remaining,remaining_ids):
    metrics={
        'remaining':remaining,'remaining_ids':remaining_ids,'effective_retained':base['effective_retained'],
        'active_rank':None,'mean_shift':None,'gpa_mean_shift':None,'mean_shift_relative':None,
        'cumulative_explained':None,'cumulative_explained_delta':None,
        'angle_max_deg':None,'angle_mean_deg':None,'principal_angles_deg':None,
        'boundary_in_repeat':False,'repeat_cluster':None,'subspace_decidable':None,
        'iterations':None,'converged':None,'determinant_min':None,
        'status':SensitivityItemStatus.COMPUTED.value,'reason':'',
    }
    if not base['has_variation']:
        metrics['status']=SensitivityItemStatus.NO_VARIANCE.value
        metrics['reason']=f'基线有效秩为0、无解析出的形变方差，保留子空间与累计解释率无从比较（噪声界{base["pca"]["noise_floor"]:.3g}）'
        return metrics
    k=base['effective_retained'];minimum=_min_remaining(k)
    if remaining<2:
        metrics['status']=SensitivityItemStatus.INSUFFICIENT.value
        metrics['reason']=f'剔除后仅剩{remaining}份，少于GPA所需的2份，无法重配准'
        return metrics
    subset={**base['snapshot'],'specimens':[s for s in base['snapshot']['specimens'] if s['id']!=specimen['id']]}
    try:
        _,gpa,pca=fit_alignment(subset,base['requested_retained'],base['tolerance'])
    except ValueError as exc:
        # Each item is an independent fit; a deterministic geometry failure is
        # reported for this specimen, leaving other items comparable.
        metrics['status']=SensitivityItemStatus.FAILED.value
        metrics['reason']=f'复用冻结输入重配准失败：{exc}'
        return metrics
    active=int(pca['active_rank']);metrics['active_rank']=active
    metrics['iterations']=int(gpa['iterations']);metrics['converged']=bool(gpa['converged'])
    metrics['determinant_min']=float(min(gpa['determinants'])) if gpa['determinants'] else None
    shift=float(np.linalg.norm(np.asarray(pca['arithmetic_mean'],dtype=float)-base['mean']))
    metrics['mean_shift']=shift
    metrics['gpa_mean_shift']=float(np.linalg.norm(np.asarray(gpa['gpa_mean'],dtype=float)-base['gpa_mean']))
    metrics['mean_shift_relative']=shift/max(base['mean_norm'],np.finfo(float).eps)
    if active<=0:
        metrics['status']=SensitivityItemStatus.NO_VARIANCE.value
        metrics['reason']='剔除后剩余样本的有效变化低于机器精度界，无可解析形变方差'
        return metrics
    if remaining<minimum:
        metrics['status']=SensitivityItemStatus.INSUFFICIENT.value
        metrics['reason']=f'剩余{remaining}份，少于保留{k}个模态所需的{minimum}份；均值偏移可比，累计解释率变化与子空间夹角不可比'
        return metrics
    if active<k:
        metrics['status']=SensitivityItemStatus.INSUFFICIENT.value
        metrics['reason']=f'剩余样本有效秩{active}低于保留{k}个模态，无法按同一k比较累计解释率与子空间夹角；均值偏移仍可比'
        return metrics
    sub_cum=float(sum(pca['explained_ratio'][:k]))
    metrics['cumulative_explained']=sub_cum
    metrics['cumulative_explained_delta']=sub_cum-base['cumulative_explained']
    angles=_principal_angles_deg(base['pca']['right_singular_vectors'],pca['right_singular_vectors'],k)
    if angles:
        metrics['angle_max_deg']=angles['max_deg'];metrics['angle_mean_deg']=angles['mean_deg']
        metrics['principal_angles_deg']=angles['angles_deg']
    clusters=_near_repeat_clusters(pca['singular_values'],active)
    boundary=_boundary_cluster(clusters,k)
    metrics['boundary_in_repeat']=bool(boundary)
    metrics['repeat_cluster']=boundary['singular_values'] if boundary else None
    if boundary or base['boundary_cluster']:
        metrics['subspace_decidable']=False;metrics['status']=SensitivityItemStatus.UNDETERMINED.value
        if boundary and base['boundary_cluster']:
            metrics['reason']='剔除后与基线的保留边界都落在近重根簇中；主子空间夹角已计算但不可判定'
        elif boundary:
            metrics['reason']='剔除后保留边界落在近重根簇中；主子空间夹角已计算但不可判定'
        else:
            metrics['reason']='基线保留边界落在近重根簇中；主子空间夹角已计算但不可判定'
    else:
        metrics['subspace_decidable']=True
    return metrics

def _influence(metrics):
    """Normalized largest observed change; returns (value, basis, partial).

    Mean shift is scaled by the baseline mean norm, the cumulative ratio change
    is already dimensionless, and the principal angle is scaled by 90 degrees.
    Items with sample shortage or no variance can still be ordered by the
    metrics that remain, but are marked partial rather than silently dropped.
    """
    values=[];basis=[]
    if metrics.get('mean_shift_relative') is not None:
        values.append(metrics['mean_shift_relative']);basis.append('均值偏移')
    if metrics.get('cumulative_explained_delta') is not None:
        values.append(abs(metrics['cumulative_explained_delta']));basis.append('累计解释率')
    if metrics.get('subspace_decidable') and metrics.get('angle_max_deg') is not None:
        values.append(metrics['angle_max_deg']/90.0);basis.append('子空间夹角')
    if not values:return None,'',True
    partial=len(basis)<3
    return max(values),' + '.join(basis),partial

def _item_view(item,influence=None,basis='',partial=True,rank=None):
    """Rank and influence are derived at read time from the frozen baseline."""
    metrics=dict(item.metrics or {})
    metrics['rank']=rank;metrics['influence']=influence
    metrics['influence_basis']=basis;metrics['influence_partial']=partial
    return {'id':item.id,'specimen_id':item.specimen_id,'code':item.code,'name':item.name,
        'status':item.status,'reason':item.reason,'metrics':metrics}

def _order_items(items):
    scored={id(i):_influence(i.metrics or {}) for i in items}
    ranked=sorted((i for i in items if scored[id(i)][0] is not None),key=lambda i:(-scored[id(i)][0],str(i.code)))
    unranked=sorted((i for i in items if scored[id(i)][0] is None),key=lambda i:str(i.code))
    views=[_item_view(item,scored[id(item)][0],scored[id(item)][1],scored[id(item)][2],position) for position,item in enumerate(ranked,1)]
    views.extend(_item_view(item) for item in unranked)
    return views

def _counts(views):
    counts={status.value:0 for status in SensitivityItemStatus}
    for view in views:counts[view['status']]=counts.get(view['status'],0)+1
    counts['total']=len(views);return counts

def _summary(base,items,run):
    views=_order_items(items)
    clusters=[{'start':c['start'],'end':c['end'],'singular_values':c['singular_values'],
        'max_relative_gap':c['max_relative_gap']} for c in base['clusters']]
    ranked=[v for v in views if v['metrics']['rank'] is not None]
    # Prefer a fully comparable specimen for the headline; a partial comparison
    # (sample shortage or no variance) only becomes headline if nothing else ranks.
    full=[v for v in ranked if not v['metrics']['influence_partial']]
    most=(full or ranked)[0] if ranked else None
    return {
        'basis_source':BASIS_SOURCE,'fingerprint_verified':True,'basis_fingerprint':base['fingerprint'],
        'specimen_count':len(base['snapshot']['specimens']),'effective_retained':base['effective_retained'],
        'requested_retained':base['requested_retained'],'min_remaining':_min_remaining(base['effective_retained']),
        'baseline':{'active_rank':base['active_rank'],'has_variation':base['has_variation'],
            'mean_norm':base['mean_norm'],'gpa_mean_norm':float(np.linalg.norm(base['gpa_mean'])),
            'retained_explained_ratio':base['cumulative_explained'],
            'singular_values':base['pca']['singular_values'],
            'boundary_in_repeat':bool(base['boundary_cluster']),'boundary_cluster':base['boundary_cluster']},
        'near_repeat':{'relative_gap_threshold':NEAR_REPEAT_REL_GAP,'clusters':clusters,
            'note':'保留边界未落在近重根簇时，簇内单轴方向不唯一但保留子空间唯一，按子空间比较夹角；边界落在簇中时夹角不可判定。'},
        'ranking_metric':'max( 均值偏移/基线均值范数, |累计解释率变化|, 最大主子空间夹角/90° )；样本不足或无可解析方差的裁片只用仍可比的指标排序并标注为部分比较',
        'counts':_counts(views),'ranked_count':len(ranked),'item_total':len(views),
        'most_influential':{'code':most['code'],'specimen_id':most['specimen_id'],
            'influence':most['metrics']['influence'],'basis':most['metrics']['influence_basis'],
            'partial':most['metrics']['influence_partial'],'status':most['status']} if most else None,
        'partial_ranked':sum(1 for v in views if v['metrics']['rank'] is not None and v['metrics']['influence_partial']),
        'published_result_untouched':True,'run_result_fingerprint':run.result.get('fingerprint'),
    }

def _diagnostic_view(db,run,d):
    items=db.scalars(select(SensitivityItem).where(SensitivityItem.diagnostic_id==d.id).order_by(SensitivityItem.id)).all()
    views=_order_items(items)
    summary=dict(d.summary or {})
    summary['counts']=_counts(views);summary['item_total']=len(views)
    if 'specimen_count' not in summary:summary['specimen_count']=d.total
    return {'id':d.id,'run_id':d.run_id,'status':d.status,'basis_fingerprint':d.basis_fingerprint,
        'total':d.total,'retained':d.retained,'effective_retained':d.effective_retained,
        'tolerance':d.tolerance,'summary':summary,'error':d.error,
        'created_at':d.created_at,'updated_at':d.updated_at,'items':views}

def request_diagnostic(db,run_id):
    run=get_run(db,run_id)
    if run.status!=RunStatus.COMPLETED:raise HTTPException(409,'仅已完成的冻结研究可做逐片剔除敏感性诊断')
    existing=db.scalar(select(SensitivityDiagnostic).where(SensitivityDiagnostic.run_id==run_id))
    if existing:
        # A failed diagnosis is resumed in place: stored items are reused and no
        # new row is created. Completed/queued/running ones are returned as-is.
        if existing.status==DiagnosticStatus.FAILED:
            existing.status=DiagnosticStatus.QUEUED;existing.error='';existing.updated_at=datetime.utcnow()
            db.commit()
            try:redis.lpush('shapes:sensitivity',str(existing.id))
            except Exception:pass
        return existing
    try:base=_baseline(run);base['tolerance']=run.tolerance
    except (KeyError,ValueError) as exc:raise HTTPException(422,f'无法建立诊断依据：{exc}')
    d=SensitivityDiagnostic(run_id=run_id,status=DiagnosticStatus.QUEUED,
        basis_fingerprint=base['fingerprint'],total=len(base['snapshot']['specimens']),
        retained=base['requested_retained'],effective_retained=base['effective_retained'],
        tolerance=run.tolerance,summary=_summary(base,[],run))
    db.add(d)
    try:db.commit()
    except IntegrityError:
        db.rollback();existing=db.scalar(select(SensitivityDiagnostic).where(SensitivityDiagnostic.run_id==run_id))
        if not existing:raise HTTPException(409,'诊断建立冲突，请重试')
        return existing
    try:redis.lpush('shapes:sensitivity',str(d.id))
    except Exception:pass
    return d

def get_view(db,run_id):
    run=get_run(db,run_id)
    d=db.scalar(select(SensitivityDiagnostic).where(SensitivityDiagnostic.run_id==run_id))
    if not d:return {'exists':False,'run_id':run_id}
    return {'exists':True,'diagnostic':_diagnostic_view(db,run,d)}

def execute_diagnostic(db,diagnostic_id):
    """Idempotent worker entry point: picks up queued/running rows, skips stored items."""
    d=db.get(SensitivityDiagnostic,diagnostic_id)
    if not d or d.status not in (DiagnosticStatus.QUEUED,DiagnosticStatus.RUNNING):return
    d.status=DiagnosticStatus.RUNNING;db.commit()
    try:
        run=get_run(db,d.run_id)
        base=_baseline(run);base['tolerance']=d.tolerance
        if base['fingerprint']!=d.basis_fingerprint:raise ValueError('诊断依据指纹与创建时不一致，拒绝续跑')
        existing={item.specimen_id for item in db.scalars(select(SensitivityItem).where(SensitivityItem.diagnostic_id==d.id)).all()}
        specimens=sorted(base['snapshot']['specimens'],key=lambda s:s['code'])
        remaining=len(specimens)-1;remaining_ids=[s['id'] for s in specimens]
        for specimen in specimens:
            if specimen['id'] in existing:continue
            try:
                metrics=_fit_item(base,specimen,remaining,[i for i in remaining_ids if i!=specimen['id']])
            except ValueError as exc:
                # A deterministic input problem is isolated to this specimen.
                metrics={'remaining':remaining,'status':SensitivityItemStatus.FAILED.value,
                    'reason':f'诊断输入校验失败：{exc}','effective_retained':base['effective_retained'],
                    'rank':None,'influence':None}
            db.add(SensitivityItem(diagnostic_id=d.id,specimen_id=specimen['id'],code=specimen['code'],
                name=specimen.get('name',''),status=metrics['status'],reason=str(metrics.get('reason',''))[:600],metrics=metrics))
            db.commit()
        items=db.scalars(select(SensitivityItem).where(SensitivityItem.diagnostic_id==d.id)).all()
        d.summary=_summary(base,items,run);d.status=DiagnosticStatus.COMPLETED;d.error=''
        d.updated_at=datetime.utcnow();db.commit()
    except Exception as exc:
        db.rollback();d=db.get(SensitivityDiagnostic,diagnostic_id)
        if d:d.status=DiagnosticStatus.FAILED;d.error=str(exc)[:600];d.updated_at=datetime.utcnow();db.commit()
