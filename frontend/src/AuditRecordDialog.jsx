import React from 'react'
import Drawer from './Drawer'
import { Badge } from './components/ui/badge'

export const auditStatusLabel = status => ({ applied:'已应用', complete:'已完成', no_change:'无需调整', rejected:'已拒绝', failed:'失败' }[status] || status || '未知状态')

export default function AuditRecordDialog({ record, time, onClose }) {
  const title = record.kind === 'tune' ? 'AI 权重调整' : '综合分析报告'
  const summary = record.report?.analysis_error || record.report?.summary || record.report?.error || '暂无分析摘要。'
  return <Drawer variant="modal" className="audit-record-dialog" title={title} eyebrow="ANALYSIS RECORD" onClose={onClose}>
    <div className="audit-record-body">
      <div className="audit-record-meta"><span>{time}</span><Badge tone={['applied','complete'].includes(record.status)?'green':['failed','rejected'].includes(record.status)?'red':'amber'}>{auditStatusLabel(record.status)}</Badge></div>
      <section className="audit-record-summary"><h3>分析摘要</h3><p>{String(summary)}</p></section>
      <div className="audit-record-count">{record.kind==='tune'?`权重变更：${record.changes?.length || 0} 项`:`采样请求：${record.report?.metrics?.sampled_events ?? record.report?.sampled_events ?? 0} 条`}</div>
      <details className="audit-record-raw"><summary>查看完整记录</summary><pre>{JSON.stringify({report:record.report,changes:record.changes},null,2)}</pre></details>
    </div>
    <footer className="audit-record-footer"><button type="button" className="button primary" onClick={onClose}>关闭</button></footer>
  </Drawer>
}
