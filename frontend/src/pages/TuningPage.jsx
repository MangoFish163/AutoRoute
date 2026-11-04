import {
  FileText,
  Gauge,
  LoaderCircle,
  LockKeyhole,
  Settings2,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'

import { Badge } from '../components/ui/badge'

import Drawer from '../Drawer'
import { auditStatusLabel } from '../AuditRecordDialog'
import AuditHistoryItem from '../AuditHistoryItem'

import { FormSelect } from '../shared'

export default function TuningPage({
  aiForm,
  analyze,
  busy,
  expandedHistoryId,
  fmtTime,
  historyData,
  historyDrawer,
  historyLoading,
  loadMoreHistory,
  openHistory,
  ruleForm,
  saveAi,
  saveRules,
  setAiForm,
  setAuditRecord,
  setExpandedHistoryId,
  setHistoryDrawer,
  setRuleForm,
  setTuningSettingsOpen,
  settings,
  toggleAi,
  toggleRules,
  tune,
  tuningSettingsOpen,
}) {
  return (
    <>
      <div className="page-heading tuning-heading">
        <div>
          <h1>自动调优</h1>
          <p>按真实请求表现调整路由，并保留分析审计记录。</p>
        </div>
        <div className="heading-actions">
          <button
            className="icon-button tuning-config-button"
            title="调优配置"
            onClick={() => setTuningSettingsOpen(true)}
          >
            <Settings2 size={17} />
          </button>
        </div>
      </div>
      <div className="tuning-banner">
        <div className="tuning-orb">
          <Sparkles size={18} />
        </div>
        <div className="tuning-banner-copy">
          <b>调优策略受约束运行</b>
          <p>只分析脱敏指标；AI 建议仅调整未锁定部署的自动权重，并保留审计记录。</p>
        </div>
        <div className="tuning-banner-actions">
          <button className="button secondary" disabled={busy} onClick={tune}>
            <Sparkles size={14} />
            AI 建议调权
          </button>
          <button className="button primary" disabled={busy} onClick={analyze}>
            {busy ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}
            生成综合分析
          </button>
        </div>
      </div>
      <div className="tuning-controls">
        <section className="panel tuning-control-row">
          <div className="tuning-control-copy">
            <span className="tuning-control-icon ai">
              <Sparkles size={16} />
            </span>
            <div>
              <b>AI 分析模型</b>
              <small>{aiForm.model || '尚未配置模型'} · 用于综合分析和权重建议</small>
            </div>
          </div>
          <label className="switch" title="启用或停用 AI 分析">
            <input
              type="checkbox"
              checked={aiForm.enabled}
              onChange={(e) => toggleAi(e.target.checked)}
              disabled={busy}
            />
            <span />
          </label>
        </section>
        <section className="panel tuning-control-row">
          <div className="tuning-control-copy">
            <span className="tuning-control-icon rules">
              <ShieldCheck size={16} />
            </span>
            <div>
              <b>规则路由</b>
              <small>连续失败冷却与权重平滑调整</small>
            </div>
          </div>
          <label className="switch" title="启用或停用规则路由">
            <input
              type="checkbox"
              checked={ruleForm.enabled}
              onChange={(e) => toggleRules(e.target.checked)}
            />
            <span />
          </label>
        </section>
      </div>
      <div className="section-title-row tuning-section-title">
        <div>
          <h2>权重变更历史</h2>
          <p>规则与 AI 调整均保留调整前后数值和原因</p>
        </div>
        <button className="text-button history-link" onClick={() => openHistory('weights')}>
          查看全部 →
        </button>
      </div>
      <div className="panel audit-panel">
        {(settings?.weight_events || []).slice(0, 1).map((w) => (
          <div className="audit-row" key={w.id}>
            <div className="audit-icon">
              <Gauge size={16} />
            </div>
            <div className="audit-info">
              <b>
                {w.name} ·{' '}
                {w.source === 'ai' ? 'AI 调整' : w.source === 'manual' ? '手动重置' : '规则调权'}
              </b>
              <small>
                {fmtTime(w.created_at)} · {w.before_weight.toFixed(2)} → {w.after_weight.toFixed(2)}{' '}
                · {w.reason}
              </small>
            </div>
            <Badge tone={w.after_weight >= w.before_weight ? 'green' : 'amber'}>{w.alias}</Badge>
          </div>
        ))}
        {!settings?.weight_events?.length && <div className="empty-small">尚无权重变化。</div>}
      </div>
      <div className="section-title-row tuning-section-title">
        <div>
          <h2>分析与建议审计</h2>
          <p>最近生成的分析报告与 AI 调权调用记录</p>
        </div>
        <button className="text-button history-link" onClick={() => openHistory('audits')}>
          查看全部 →
        </button>
      </div>
      <div className="panel audit-panel">
        {(settings?.ai_audits || []).slice(0, 1).map((a) => (
          <div className="audit-row" key={a.id}>
            <div className="audit-icon">
              <FileText size={16} />
            </div>
            <div className="audit-info">
              <b>{a.kind === 'tune' ? 'AI 权重调整' : '综合分析报告'}</b>
              <small>
                {fmtTime(a.created_at)} ·{' '}
                {a.kind === 'tune'
                  ? `${a.changes?.length || 0} 项变更`
                  : `样本 ${a.report?.metrics?.sampled_events ?? a.report?.sampled_events ?? 0} 条请求`}
              </small>
            </div>
            <Badge
              tone={
                a.status === 'applied' || a.status === 'complete'
                  ? 'green'
                  : a.status === 'rejected' || a.status === 'failed'
                    ? 'red'
                    : 'amber'
              }
            >
              {auditStatusLabel(a.status)}
            </Badge>
            <button className="text-button" onClick={() => setAuditRecord(a)}>
              查看记录
            </button>
          </div>
        ))}
        {!settings?.ai_audits?.length && (
          <div className="empty-small">还没有审计记录。生成综合分析报告后可查看。</div>
        )}
      </div>
      {tuningSettingsOpen && (
        <Drawer
          title="调优配置"
          eyebrow="ROUTING SETTINGS"
          onClose={() => setTuningSettingsOpen(false)}
        >
          <div className="drawer-content">
            <form className="panel drawer-config-panel" onSubmit={saveAi}>
              <div className="panel-heading">
                <div>
                  <h2>AI 分析模型</h2>
                  <p>用于分析近期指标并生成权重建议</p>
                </div>
                <Badge tone={aiForm.enabled ? 'green' : 'amber'}>
                  {aiForm.enabled ? '已启用' : '已停用'}
                </Badge>
              </div>
              <div className="drawer-form-grid">
                <label>
                  协议
                  <FormSelect
                    value={aiForm.provider}
                    onChange={(v) =>
                      setAiForm({
                        ...aiForm,
                        provider: v,
                        base_url:
                          v === 'anthropic'
                            ? 'https://api.anthropic.com'
                            : 'https://api.openai.com/v1',
                      })
                    }
                    options={[
                      ['openai', 'OpenAI 兼容'],
                      ['anthropic', 'Anthropic'],
                    ]}
                  />
                </label>
                <label>
                  分析模型
                  <input
                    value={aiForm.model}
                    onChange={(e) => setAiForm({ ...aiForm, model: e.target.value })}
                    placeholder="例如 gpt-4.1-mini"
                  />
                </label>
                <label className="drawer-wide">
                  API Base URL
                  <input
                    value={aiForm.base_url}
                    onChange={(e) => setAiForm({ ...aiForm, base_url: e.target.value })}
                  />
                </label>
                <label className="drawer-wide">
                  API Token
                  <input
                    type="password"
                    value={aiForm.api_key}
                    onChange={(e) => setAiForm({ ...aiForm, api_key: e.target.value })}
                    placeholder={aiForm.key_saved ? '已保存，留空保持不变' : '用于 AI 分析的 Token'}
                  />
                </label>
              </div>
              <div className="drawer-form-foot">
                <span>
                  <LockKeyhole size={13} />
                  Token 使用 Windows DPAPI 加密保存
                </span>
                <button className="button primary small" disabled={busy}>
                  保存 AI 配置
                </button>
              </div>
            </form>
            <form className="panel drawer-config-panel" onSubmit={saveRules}>
              <div className="panel-heading">
                <div>
                  <h2>规则路由</h2>
                  <p>失败冷却与自动权重范围</p>
                </div>
                <Badge tone={ruleForm.enabled ? 'green' : 'neutral'}>
                  {ruleForm.enabled ? '已启用' : '已停用'}
                </Badge>
              </div>
              <div className="drawer-form-grid">
                <label>
                  连续失败阈值
                  <input
                    type="number"
                    min="3"
                    max="10"
                    value={ruleForm.failure_threshold}
                    onChange={(e) =>
                      setRuleForm({ ...ruleForm, failure_threshold: +e.target.value })
                    }
                  />
                  <small>至少连续失败 3 次，且生效权重低于 50 才允许冷却</small>
                </label>
                <label>
                  冷却时长（秒）
                  <input
                    type="number"
                    min="10"
                    max="3600"
                    value={ruleForm.cooldown_seconds}
                    onChange={(e) =>
                      setRuleForm({ ...ruleForm, cooldown_seconds: +e.target.value })
                    }
                  />
                  <small>失败先降权；权重 ≥50 时保持可重试</small>
                </label>
                <label>
                  自动权重下限
                  <input
                    type="number"
                    min="0.1"
                    max="1"
                    step="0.05"
                    value={ruleForm.min_auto_factor}
                    onChange={(e) => setRuleForm({ ...ruleForm, min_auto_factor: +e.target.value })}
                  />
                </label>
                <label>
                  自动权重上限
                  <input
                    type="number"
                    min="1"
                    max="3"
                    step="0.05"
                    value={ruleForm.max_auto_factor}
                    onChange={(e) => setRuleForm({ ...ruleForm, max_auto_factor: +e.target.value })}
                  />
                </label>
              </div>
              <div className="drawer-form-foot">
                <span>
                  <ShieldCheck size={13} />
                  手动锁定权重优先于自动调权
                </span>
                <button className="button primary small">保存规则</button>
              </div>
            </form>
          </div>
        </Drawer>
      )}
      {historyDrawer && (
        <Drawer
          className="history-drawer"
          title={historyDrawer === 'weights' ? '权重变更历史' : '分析与建议审计'}
          eyebrow="HISTORY"
          onClose={() => setHistoryDrawer(null)}
        >
          <div className="history-drawer-content">
            {historyDrawer === 'weights'
              ? historyData.weights.items.map((w) => (
                  <article className="history-item" key={w.id}>
                    <div className="history-item-head">
                      <div className="audit-icon">
                        <Gauge size={15} />
                      </div>
                      <div className="history-item-title">
                        <b>
                          {w.name || '已删除部署'} ·{' '}
                          {w.source === 'ai'
                            ? 'AI 调整'
                            : w.source === 'manual'
                              ? '手动重置'
                              : '规则调权'}
                        </b>
                        <small>
                          {fmtTime(w.created_at)} · {w.alias || w.deployment_id}
                        </small>
                      </div>
                      <Badge tone={w.after_weight >= w.before_weight ? 'green' : 'amber'}>
                        {w.source === 'ai' ? 'AI' : w.source === 'manual' ? '手动' : '规则'}
                      </Badge>
                    </div>
                    <div className="history-weight-value">
                      {Number(w.before_weight).toFixed(2)} <span>→</span>{' '}
                      {Number(w.after_weight).toFixed(2)}
                    </div>
                    <p>{w.reason}</p>
                  </article>
                ))
              : historyData.audits.items.map((a) => (
                  <AuditHistoryItem
                    key={a.id}
                    record={a}
                    time={fmtTime(a.created_at)}
                    expanded={expandedHistoryId === a.id}
                    onToggle={() =>
                      setExpandedHistoryId((current) => (current === a.id ? null : a.id))
                    }
                  />
                ))}
            {historyLoading && <div className="history-empty">正在加载历史记录…</div>}
            {!historyLoading && !historyData[historyDrawer].items.length && (
              <div className="history-empty">暂无历史记录。</div>
            )}
            {historyData[historyDrawer].items.length < historyData[historyDrawer].total && (
              <button
                className="button secondary history-load-more"
                disabled={historyLoading}
                onClick={loadMoreHistory}
              >
                {historyLoading ? '正在加载…' : '加载更早记录'}
              </button>
            )}
          </div>
          <div className="history-footer">共 {historyData[historyDrawer].total} 条记录</div>
        </Drawer>
      )}
    </>
  )
}
