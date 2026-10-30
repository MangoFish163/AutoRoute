import React from 'react'

const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null
const compact = value => value == null ? '—' : value >= 1000000 ? `${Number((value / 1000000).toFixed(2))}M` : value >= 1000 ? `${Number((value / 1000).toFixed(2))}K` : String(value)

export default function TokenUsage({event}) {
  const usage = event.usage || {}
  const input = count(event.input_tokens ?? usage.prompt_tokens ?? usage.input_tokens)
  const output = count(event.output_tokens ?? usage.completion_tokens ?? usage.output_tokens)
  const total = count(event.total_tokens ?? usage.total_tokens) ?? (input != null && output != null ? input + output : null)
  if (total == null && input == null && output == null) return <span className="token-usage-empty" title="上游未返回用量或此历史记录没有保存用量">N/A</span>
  const complete = input != null && output != null
  const sum = complete ? input + output : 0
  const inputPercent = sum > 0 ? input / sum * 100 : 0
  const detail = complete ? `输入 ${input.toLocaleString()} (${inputPercent.toFixed(1)}%)；输出 ${output.toLocaleString()} (${sum ? (100 - inputPercent).toFixed(1) : '0.0'}%)` : '此记录缺少输入/输出明细'
  return <div className="token-usage-cell" title={`总计 ${total?.toLocaleString() ?? '未知'} tokens\n${detail}`}>
    <div className="token-usage-top"><strong>{compact(total)}</strong>{complete && <div className="token-usage-bar" role="img" aria-label={detail}><i style={{width: `${inputPercent}%`}}/><i style={{width: `${sum ? 100-inputPercent : 0}%`}}/></div>}</div>
    <div className="token-usage-split">{complete ? <><span>输入 {compact(input)}</span><span className="token-usage-separator">/</span><span>输出 {compact(output)}</span></> : <span className="token-usage-unavailable">无输入/输出明细</span>}</div>
  </div>
}
