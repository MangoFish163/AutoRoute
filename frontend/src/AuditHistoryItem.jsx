import React, { useEffect, useId, useState } from 'react'
import { Check, ChevronDown, Copy, FileText } from 'lucide-react'
import { Badge } from './components/ui/badge'
import { auditStatusLabel } from './AuditRecordDialog'

export default function AuditHistoryItem({ record, time, expanded, onToggle }) {
  const detailId = useId()
  const [copyStatus, setCopyStatus] = useState('')
  const content = JSON.stringify({ report: record.report, changes: record.changes }, null, 2)
  const title = record.kind === 'tune' ? 'AI 权重调整' : '综合分析报告'
  useEffect(() => {
    setCopyStatus('')
  }, [expanded, record.id])
  useEffect(() => {
    if (!copyStatus) return
    const timer = setTimeout(() => setCopyStatus(''), 2500)
    return () => clearTimeout(timer)
  }, [copyStatus])
  const copyContent = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopyStatus('copied')
    } catch {
      setCopyStatus('failed')
    }
  }
  return (
    <article className={`history-item audit-history-item ${expanded ? 'is-expanded' : ''}`}>
      <button
        type="button"
        className="history-item-toggle"
        aria-expanded={expanded}
        aria-controls={detailId}
        onClick={onToggle}
      >
        <span className="history-item-head">
          <span className="audit-icon">
            <FileText size={15} />
          </span>
          <span className="history-item-title">
            <b>{title}</b>
            <small>
              {time} ·{' '}
              {record.kind === 'tune'
                ? `${record.changes?.length || 0} 项变更`
                : `样本 ${record.report?.metrics?.sampled_events ?? record.report?.sampled_events ?? 0} 条请求`}
            </small>
          </span>
          <Badge
            tone={
              ['applied', 'complete'].includes(record.status)
                ? 'green'
                : ['failed', 'rejected'].includes(record.status)
                  ? 'red'
                  : 'amber'
            }
          >
            {auditStatusLabel(record.status)}
          </Badge>
          <span className="history-item-action">
            {expanded ? '收起' : '展开'}
            <ChevronDown size={14} aria-hidden="true" />
          </span>
        </span>
        <span className="history-summary">
          {record.report?.analysis_error ||
            record.report?.summary ||
            record.report?.error ||
            '暂无摘要'}
        </span>
      </button>
      <div id={detailId} hidden={!expanded}>
        {expanded && (
          <div className="history-json-wrap">
            <div className="history-json-toolbar">
              <span role="status" className={copyStatus === 'failed' ? 'copy-failed' : ''}>
                {copyStatus === 'copied'
                  ? '已复制到剪贴板'
                  : copyStatus === 'failed'
                    ? '复制失败，请重试'
                    : '完整记录'}
              </span>
              <button
                type="button"
                className="history-copy-button"
                aria-label="复制完整记录"
                title="复制完整记录"
                onClick={copyContent}
              >
                {copyStatus === 'copied' ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
            <pre className="history-json">{content}</pre>
          </div>
        )}
      </div>
    </article>
  )
}
