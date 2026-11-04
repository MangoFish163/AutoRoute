import { Activity, Radio, Search } from 'lucide-react'

import { Badge } from '../components/ui/badge'

import TokenUsage from '../TokenUsage'
import RequestTrend from '../RequestTrend'
import Drawer from '../Drawer'

import { formatDuration, DateRangePicker } from '../shared'

export default function LogsPage({
  events,
  fmtTime,
  hasMore,
  loading,
  logEnd,
  logFiltered,
  logPage,
  logPageCount,
  logPageRows,
  logQuery,
  logRangeManuallyChanged,
  logStart,
  logStats,
  logsLive,
  logStatusOpen,
  setLogPage,
  setLogQuery,
  setLogRange,
  setLogStatusOpen,
  setLogsLive,
  setSelectedEvent,
}) {
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">OBSERVABILITY</div>
          <h1>请求日志</h1>
          <p>从请求趋势、失败类型、延迟和路由尝试中定位问题。</p>
        </div>
        <button className="button secondary" onClick={() => setLogStatusOpen(true)}>
          <Activity size={15} />
          查看状态
        </button>
      </div>
      <div className="logs-toolbar">
        <div className="logs-search">
          <Search size={16} />
          <input
            value={logQuery}
            onChange={(e) => {
              setLogQuery(e.target.value)
              setLogPage(1)
            }}
            placeholder="搜索请求 ID、模型、供应商、部署或错误"
          />
          <button className={logsLive ? 'live-active' : ''} onClick={() => setLogsLive((v) => !v)}>
            <Radio size={14} />
            {logsLive ? '实时刷新中' : '实时刷新'}
          </button>
        </div>
        <DateRangePicker
          start={logStart}
          end={logEnd}
          onChange={(s, e) => {
            logRangeManuallyChanged.current = true
            setLogsLive(false)
            setLogRange([s, e])
            setLogPage(1)
          }}
        />
      </div>
      <RequestTrend stats={logStats} events={logFiltered} start={logStart} end={logEnd} />
      <div className="panel logs-panel">
        <div className="logs-head">
          <div>
            <b>请求事件</b>
            <small>
              显示 {logPageRows.length} / {logStats.total} 条 · 当前页 {events.length} 条 ·
              正文不保存
            </small>
          </div>
          <Badge tone="green" dot>
            隐私保护已启用
          </Badge>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>协议</th>
                <th>逻辑模型</th>
                <th>部署</th>
                <th>供应商</th>
                <th>耗时</th>
                <th>Token 用量</th>
                <th>结果 / 状态码</th>
              </tr>
            </thead>
            <tbody>
              {logPageRows.map((ev) => (
                <tr
                  key={ev.id}
                  onClick={() => {
                    setSelectedEvent(ev)
                  }}
                  className="clickable-row"
                >
                  <td className="mono subtle">{fmtTime(ev.created_at)}</td>
                  <td>
                    <Badge>{ev.protocol === 'anthropic' ? 'Anthropic' : 'OpenAI'}</Badge>
                  </td>
                  <td>
                    <code>{ev.alias || '—'}</code>
                  </td>
                  <td>{ev.deployment_name || '—'}</td>
                  <td
                    title={
                      ev.supplier_source === 'current_deployment'
                        ? '根据当前部署推断，历史记录未保存供应商快照'
                        : ev.supplier_name || '未匹配到供应商'
                    }
                  >
                    {ev.supplier_name || '—'}
                    {ev.supplier_source === 'current_deployment' && (
                      <small className="supplier-inferred">推断</small>
                    )}
                  </td>
                  <td className="mono">{formatDuration(ev.latency_ms)}</td>
                  <td>
                    <TokenUsage event={ev} />
                  </td>
                  <td>
                    <Badge tone={ev.status === 'success' ? 'green' : 'red'} dot>
                      {ev.status === 'success' ? '成功' : `失败 · ${ev.status_code || '—'}`}
                    </Badge>
                  </td>
                </tr>
              ))}
              {!logFiltered.length && (
                <tr>
                  <td colSpan="8">
                    <div className="empty-small">
                      {events.length
                        ? '没有符合当前搜索和筛选条件的请求。'
                        : '尚无请求。配置完成后，客户端请求会显示在这里。'}
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="panel-footer">
          <span>{'按所选时间区间分页加载'} · 点击行查看完整路由尝试</span>
          <div className="log-pagination">
            <button disabled={loading || logPage <= 1} onClick={() => setLogPage((p) => p - 1)}>
              上一页
            </button>
            <span>
              {logPage} / {logPageCount}
            </span>
            <button disabled={loading || !hasMore} onClick={() => setLogPage((p) => p + 1)}>
              下一页
            </button>
          </div>
        </div>
      </div>
      {logStatusOpen && (
        <Drawer title="请求状态" eyebrow="REQUEST STATUS" onClose={() => setLogStatusOpen(false)}>
          <div className="log-status-body">
            <div className="log-status-live">
              <span className={logsLive ? 'status-dot' : 'status-dot status-off'} />
              <div>
                <b>{logsLive ? '实时更新中' : '实时刷新已关闭'}</b>
                <small>
                  {logsLive ? '新请求写入后自动更新' : '开启实时刷新后，新请求会自动显示'}
                </small>
              </div>
              <button
                className={`button ${logsLive ? 'secondary' : 'primary'} small`}
                onClick={() => setLogsLive((v) => !v)}
              >
                <Radio size={13} />
                {logsLive ? '停止实时' : '开启实时'}
              </button>
            </div>
            <div className="log-status-grid">
              <div className="log-stat">
                <small>请求数</small>
                <b>{logStats.total}</b>
              </div>
              <div className="log-stat">
                <small>成功率</small>
                <b>{logStats.rate.toFixed(1)}%</b>
              </div>
              <div className="log-stat">
                <small>Token 消耗</small>
                <b>{logStats.tokenEvents ? logStats.tokens.toLocaleString() : '—'}</b>
                <span>
                  {logStats.tokenEvents
                    ? `${logStats.tokenEvents} 条请求有用量数据`
                    : '等待新请求返回用量'}
                </span>
              </div>
              <div className="log-stat">
                <small>平均耗时</small>
                <b>{logStats.avg ? `${(logStats.avg / 1000).toFixed(2)} s` : '—'}</b>
              </div>
              <div className="log-stat">
                <small>P95 耗时</small>
                <b>{logStats.p95 ? `${(logStats.p95 / 1000).toFixed(2)} s` : '—'}</b>
              </div>
            </div>
            <p className="log-status-note">
              统计范围跟随请求日志当前筛选条件。历史请求没有保存 Token
              用量，因此只计入升级后产生且上游返回 usage 的请求。
            </p>
          </div>
        </Drawer>
      )}
    </>
  )
}
