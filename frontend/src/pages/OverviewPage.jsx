import { useState } from 'react'

import { CircleHelp } from 'lucide-react'

import Drawer from '../Drawer'

import { compactCount, DateRangePicker } from '../shared'

export default function OverviewPage({
  aliases,
  logEnd,
  logRangeManuallyChanged,
  logStart,
  overview,
  setLogPage,
  setLogRange,
  summary,
}) {
  const [overviewDrawer, setOverviewDrawer] = useState(null)
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">USAGE ANALYTICS</div>
          <h1>服务概览</h1>
          <p>按请求日志的时间区间统计供应商用量、实际响应延迟与模型使用情况</p>
        </div>
        <div className="overview-actions">
          <DateRangePicker
            start={logStart}
            end={logEnd}
            onChange={(s, e) => {
              logRangeManuallyChanged.current = true
              setLogRange([s, e])
              setLogPage(1)
            }}
          />
        </div>
      </div>
      {(() => {
        const recent = { length: overview?.stats?.total || 0 }
        const providers = overview?.providers || [],
          models = overview?.models || []
        const tokens = providers.reduce((n, x) => n + x.tokens, 0),
          latencyCount = providers.reduce((n, x) => n + x.latencyCount, 0),
          latency = latencyCount ? providers.reduce((n, x) => n + x.latency, 0) / latencyCount : 0
        const chart = (title, desc, rows, field, max, fmt, total) => (
          <section
            className="panel overview-chart"
            role="button"
            tabIndex={0}
            onClick={() =>
              setOverviewDrawer({
                title,
                total,
                rows: [...rows].sort((a, b) => b[field] - a[field]),
                field,
                format: fmt,
              })
            }
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                setOverviewDrawer({
                  title,
                  total,
                  rows: [...rows].sort((a, b) => b[field] - a[field]),
                  field,
                  format: fmt,
                })
              }
            }}
            aria-label={`${title}，点击查看全部记录`}
          >
            <header>
              <div>
                <h2>{title}</h2>
                <p>{desc}</p>
              </div>
              <b title={total}>{total}</b>
            </header>
            {rows.length ? (
              <div className="overview-bars">
                {[...rows]
                  .sort((a, b) => b[field] - a[field])
                  .slice(0, 6)
                  .map((r) => (
                    <div className="overview-bar-row" key={r.name}>
                      <span title={r.name}>{r.name}</span>
                      <div>
                        <i style={{ width: `${Math.max(2, (r[field] / max) * 100)}%` }} />
                      </div>
                      <b title={fmt(r[field])}>{fmt(r[field])}</b>
                    </div>
                  ))}
              </div>
            ) : (
              <div className="overview-empty">当前时间区间暂无可展示的请求数据</div>
            )}
          </section>
        )
        const tokenRows = providers.map((x) => ({ ...x, value: x.tokens })),
          latencyRows = providers
            .filter((x) => x.latencyCount)
            .map((x) => ({ ...x, value: x.latency / x.latencyCount }))
            .sort((a, b) => b.value - a.value),
          modelRows = models.map((x) => ({ ...x, value: x.count })),
          modelTokenRows = models
            .map((x) => ({ ...x, value: x.tokens }))
            .sort((a, b) => b.value - a.value)
        const successCount = (overview?.stats?.total || 0) - (overview?.stats?.failed || 0),
          successRate = recent.length ? (successCount / recent.length) * 100 : 0
        return (
          <>
            <div className="overview-kpis">
              <div>
                <span className="overview-kpi-label">
                  可用部署{' '}
                  <span
                    className="overview-help"
                    tabIndex="0"
                    data-tooltip="当前已启用、可参与请求路由的模型部署数量。"
                    aria-label="当前已启用、可参与请求路由的模型部署数量。"
                  >
                    <CircleHelp size={13} />
                  </span>
                </span>
                <b>{summary?.deployments?.enabled || 0}</b>
                <small>共 {summary?.deployments?.total || 0} 个部署</small>
              </div>
              <div>
                <span className="overview-kpi-label">
                  请求成功率{' '}
                  <span
                    className="overview-help"
                    tabIndex="0"
                    data-tooltip="当前选择的时间区间内，成功请求数占全部已记录请求数的比例。"
                    aria-label="当前选择的时间区间内，成功请求数占全部已记录请求数的比例。"
                  >
                    <CircleHelp size={13} />
                  </span>
                </span>
                <b>{successRate.toFixed(1)}%</b>
                <small>
                  成功 {successCount} / 共 {recent.length} 次请求
                </small>
              </div>
              <div>
                <span className="overview-kpi-label">
                  冷却中的部署{' '}
                  <span
                    className="overview-help"
                    tabIndex="0"
                    data-tooltip="因近期请求失败而暂时停止路由、等待自动恢复的部署数量。"
                    aria-label="因近期请求失败而暂时停止路由、等待自动恢复的部署数量。"
                  >
                    <CircleHelp size={13} />
                  </span>
                </span>
                <b>{summary?.cooldowns || 0}</b>
                <small>自动恢复中</small>
              </div>
              <div>
                <span className="overview-kpi-label">
                  逻辑模型{' '}
                  <span
                    className="overview-help"
                    tabIndex="0"
                    data-tooltip="已配置的不同逻辑模型别名数量，客户端可通过这些别名发起请求。"
                    aria-label="已配置的不同逻辑模型别名数量，客户端可通过这些别名发起请求。"
                  >
                    <CircleHelp size={13} />
                  </span>
                </span>
                <b>{aliases.length}</b>
                <small>统一 API 别名</small>
              </div>
            </div>
            <div className="overview-grid">
              {chart(
                '服务商的令牌使用情况',
                '按供应商汇总 Token',
                tokenRows,
                'value',
                Math.max(1, ...tokenRows.map((x) => x.value)),
                (n) => compactCount(n),
                `${compactCount(tokens)} tokens`,
              )}
              {chart(
                '模型 Token 使用情况',
                '按逻辑模型统计 Token 消费量',
                modelTokenRows,
                'value',
                Math.max(1, ...modelTokenRows.map((x) => x.value)),
                (n) => compactCount(n),
                `${compactCount(modelTokenRows.reduce((n, x) => n + x.value, 0))} tokens`,
              )}
              {chart(
                '模型的使用情况',
                '按逻辑模型统计请求次数',
                modelRows,
                'value',
                Math.max(1, ...modelRows.map((x) => x.value)),
                (n) => `${n} 次`,
                `${recent.length} 次请求`,
              )}
              {chart(
                '服务商的实际延迟情况',
                '按请求记录计算平均端到端延迟',
                latencyRows,
                'value',
                Math.max(1, ...latencyRows.map((x) => x.value)),
                (n) => `${(n / 1000).toFixed(2)} 秒`,
                latency ? `${(latency / 1000).toFixed(2)} 秒` : '—',
              )}
            </div>
            <div className="overview-footnote">
              统计范围与请求日志共用时间区间；延迟来自实际请求记录，Token 仅计入上游返回用量的请求。
            </div>
          </>
        )
      })()}
      {overviewDrawer && (
        <Drawer
          className="overview-drawer"
          title={overviewDrawer.title}
          eyebrow="统计明细"
          description={`当前时间区间 · ${overviewDrawer.rows.length} 条记录`}
          onClose={() => setOverviewDrawer(null)}
        >
          <div className="drawer-content overview-drawer-content">
            <div className="overview-drawer-total">
              合计 <b>{overviewDrawer.total}</b>
            </div>
            <div className="overview-drawer-list">
              {overviewDrawer.rows.length ? (
                overviewDrawer.rows.map((r, i) => (
                  <div className="overview-drawer-row" key={r.name}>
                    <span className="overview-drawer-rank">{i + 1}</span>
                    <span className="overview-drawer-name" title={r.name}>
                      {r.name}
                    </span>
                    <b title={Number(r[overviewDrawer.field]).toLocaleString()}>
                      {overviewDrawer.format(r[overviewDrawer.field])}
                    </b>
                  </div>
                ))
              ) : (
                <div className="overview-empty">当前时间区间暂无记录</div>
              )}
            </div>
          </div>
        </Drawer>
      )}
    </>
  )
}
