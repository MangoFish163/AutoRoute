import React, {useMemo, useState} from 'react'
import {ChevronDown} from 'lucide-react'

export function trendBuckets(events, start, end) {
  const hour = 3600000
  const plotStart = Math.floor(start / hour) * hour
  const plotEnd = Math.max(plotStart + hour, Math.ceil(end / hour) * hour)
  const step = (plotEnd - plotStart) / 10
  const time = ms => new Date(ms).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false})
  const buckets = Array.from({length:10}, (_, i) => ({label:`${time(plotStart + step*i)} — ${time(plotStart + step*(i+1))}`, success:0, error:0}))
  for (const event of events) {
    const t = new Date(event.created_at).getTime()
    if (!Number.isFinite(t) || t < start || t > end) continue
    const index = Math.max(0, Math.min(9, Math.floor((t-plotStart)/step)))
    buckets[index][event.status === 'success' ? 'success' : 'error']++
  }
  return buckets
}

export default function RequestTrend({events, start, end}) {
  const [expanded, setExpanded] = useState(false)
  const success = events.filter(event => event.status === 'success').length
  const buckets = useMemo(() => expanded ? trendBuckets(events, start, end) : [], [expanded, events, start, end])
  const max = Math.max(1, ...buckets.map(bucket => bucket.success + bucket.error))
  return <section className={`panel request-trend ${expanded ? 'is-expanded' : ''}`}>
    <button type="button" className="request-trend-toggle" aria-expanded={expanded} aria-controls="request-trend-chart" onClick={() => setExpanded(value => !value)}>
      <span className="request-trend-title"><b>请求趋势</b><small>所选时间范围</small></span>
      <span className="request-trend-counts"><span>总计 <strong>{events.length.toLocaleString()}</strong></span><span className="success">成功 <strong>{success.toLocaleString()}</strong></span><span className="error">失败 <strong>{(events.length-success).toLocaleString()}</strong></span></span>
      <span className="request-trend-action">{expanded ? '收起趋势' : '展开趋势'}<ChevronDown size={14}/></span>
    </button>
    {expanded && <div id="request-trend-chart" className="request-trend-chart"><div className="log-bars">{buckets.map((bucket, index) => <div className="log-bucket" key={index} data-tooltip={`${bucket.label}\n成功：${bucket.success}\n失败：${bucket.error}`}><div className="log-bar-stack" style={{height:`${Math.max(3, (bucket.success+bucket.error)/max*100)}%`}}><i className="ok" style={{flex:bucket.success}}/><i className="err" style={{flex:bucket.error}}/></div></div>)}</div></div>}
  </section>
}
