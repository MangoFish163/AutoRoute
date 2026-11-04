import { formatDuration, initials, providerColor } from './shared'

import { useState } from 'react'

import { Copy, Settings2 } from 'lucide-react'

import TokenUsage from './TokenUsage'
import Drawer from './Drawer'

export function ModelDetails({
  baseUrl,
  copy,
  openEdit,
  selectedModel,
  setSelectedModel,
  toggleDeployment,
}) {
  const [modelDetailTab, setModelDetailTab] = useState('info')
  return (
    <Drawer
      className="model-detail-drawer"
      title={selectedModel.name}
      eyebrow="MODEL DEPLOYMENT"
      onClose={() => setSelectedModel(null)}
    >
      <div className="model-detail-intro">
        <div className={`provider-avatar ${providerColor(selectedModel.provider)}`}>
          {initials(selectedModel.name)}
        </div>
        <div>
          <b>{selectedModel.alias}</b>
          <small>
            {selectedModel.provider === 'anthropic'
              ? 'Anthropic'
              : selectedModel.provider === 'responses'
                ? 'Responses'
                : 'OpenAI'}{' '}
            兼容格式 · {selectedModel.upstream_model}
          </small>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={selectedModel.enabled}
          aria-label={selectedModel.enabled ? '停用模型' : '启用模型'}
          className={`model-enabled-switch ${selectedModel.enabled ? 'is-enabled' : ''}`}
          onClick={() => toggleDeployment(selectedModel)}
        >
          <span />
          {selectedModel.enabled ? '已启用' : '已停用'}
        </button>
      </div>
      <nav className="event-detail-tabs model-detail-tabs">
        {[
          ['info', '基础信息'],
          ['config', '配置'],
          ['guide', '接入教程'],
        ].map(([id, label]) => (
          <button
            key={id}
            className={modelDetailTab === id ? 'active' : ''}
            onClick={() => setModelDetailTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="event-drawer-content model-detail-content">
        {modelDetailTab === 'info' && (
          <>
            <h3>模型信息</h3>
            <div className="event-info-list">
              {[
                ['部署名称', selectedModel.name],
                ['逻辑模型', selectedModel.alias],
                ['上游模型', selectedModel.upstream_model],
                ['状态', selectedModel.health || (selectedModel.enabled ? '运行正常' : '已停用')],
                ['生效权重', Number(selectedModel.effective_weight || 0).toFixed(2)],
                ['速率限制', selectedModel.rpm ? `${selectedModel.rpm} RPM` : '未设置'],
                ['凭据数量', `${selectedModel.key_count || 0} 个`],
              ].map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <code>{value ?? '—'}</code>
                </div>
              ))}
            </div>
            <p className="field-hint">凭据内容不会在详情中显示。</p>
          </>
        )}
        {modelDetailTab === 'config' && (
          <>
            <h3>当前配置</h3>
            <div className="event-info-list">
              {[
                ['上游地址', selectedModel.base_url],
                [
                  '代理',
                  selectedModel.use_system_proxy
                    ? selectedModel.proxy_url?.trim() || '使用系统代理'
                    : '直连',
                ],
                ['超时时间', `${selectedModel.timeout_seconds ?? 90} 秒`],
                ['最大输入 Token', selectedModel.max_input_tokens || '未设置'],
                ['最大输出 Token', selectedModel.max_output_tokens || '未设置'],
                ['思考模式', selectedModel.thinking_mode ? '开启' : '关闭'],
                ['权重锁定', selectedModel.weight_locked ? '已锁定' : '自动调节'],
                ['备注', selectedModel.note || '—'],
              ].map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <code>{value ?? '—'}</code>
                </div>
              ))}
            </div>
            <button
              className="button secondary model-edit-button"
              onClick={() => {
                setSelectedModel(null)
                openEdit(selectedModel)
              }}
            >
              <Settings2 size={14} />
              编辑配置
            </button>
          </>
        )}
        {modelDetailTab === 'guide' && (
          <>
            <h3>客户端接入</h3>
            <p className="model-guide-copy">
              将客户端的 API 地址指向本地模型路由器，并使用逻辑模型名称。
            </p>
            <div className="event-info-list">
              <div>
                <span>API 地址</span>
                <code>{baseUrl}</code>
              </div>
              <div>
                <span>模型名称</span>
                <code>{selectedModel.alias}</code>
              </div>
              <div>
                <span>API Key</span>
                <code>使用本地路由器配置的客户端密钥</code>
              </div>
            </div>
            <h3 className="model-guide-subtitle">OpenAI 兼容示例</h3>
            <div className="model-code-wrap">
              <button
                className="model-code-copy"
                onClick={() =>
                  copy(
                    [
                      'from openai import OpenAI',
                      '',
                      'client = OpenAI(',
                      '    base_url="' + baseUrl + '",',
                      '    api_key="YOUR_LOCAL_API_KEY",',
                      ')',
                      '',
                      'response = client.chat.completions.create(',
                      '    model="' + selectedModel.alias + '",',
                      '    messages=[{"role": "user", "content": "Hello"}],',
                      ')',
                    ].join('\n'),
                  )
                }
              >
                <Copy size={13} />
                复制
              </button>
              <pre className="model-guide-code">
                {'from openai import OpenAI\n\nclient = OpenAI(\n    base_url="' +
                  baseUrl +
                  '",\n    api_key="YOUR_LOCAL_API_KEY",\n)\n\nresponse = client.chat.completions.create(\n    model="' +
                  selectedModel.alias +
                  '",\n    messages=[{"role": "user", "content": "Hello"}],\n)'}
              </pre>
            </div>
            <h3 className="model-guide-subtitle">cURL 示例</h3>
            <div className="model-code-wrap">
              <button
                className="model-code-copy"
                onClick={() =>
                  copy(
                    'curl ' +
                      baseUrl +
                      '/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer YOUR_LOCAL_API_KEY" -d \'{"model":"' +
                      selectedModel.alias +
                      '","messages":[{"role":"user","content":"Hello"}]}\'',
                  )
                }
              >
                <Copy size={13} />
                复制
              </button>
              <pre className="model-guide-code">
                {'curl ' +
                  baseUrl +
                  '/chat/completions \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer YOUR_LOCAL_API_KEY" \\\n  -d \'{"model":"' +
                  selectedModel.alias +
                  '","messages":[{"role":"user","content":"Hello"}]}\''}
              </pre>
            </div>
            <p className="field-hint">请求会通过此逻辑模型自动路由到已配置的供应商部署。</p>
          </>
        )}
      </div>
    </Drawer>
  )
}

export function EventDetails({ copy, fmtTime, selectedEvent, setSelectedEvent }) {
  const [eventDetailTab, setEventDetailTab] = useState('overview')
  return (
    <Drawer
      className="event-drawer"
      title="请求路由详情"
      eyebrow="REQUEST TRACE"
      onClose={() => setSelectedEvent(null)}
    >
      <div className="event-drawer-summary">
        <span className={`badge ${selectedEvent.status === 'success' ? 'green' : 'red'}`}>
          <i />
          {selectedEvent.status === 'success' ? '成功' : '失败'}
        </span>
        <span className="badge blue">{selectedEvent.protocol || '未知协议'}</span>
        <code>{selectedEvent.request_id}</code>
        <button
          className="icon-button"
          aria-label="复制请求 ID"
          onClick={() => copy(selectedEvent.request_id)}
        >
          <Copy size={14} />
        </button>
      </div>
      <nav className="event-detail-tabs" aria-label="请求详情分类">
        {[
          ['overview', '概览'],
          ['attempts', `路由尝试 (${selectedEvent.attempts?.length || 0})`],
          ['error', '错误信息'],
          ['raw', '原始记录'],
        ].map(([id, label]) => (
          <button
            key={id}
            className={eventDetailTab === id ? 'active' : ''}
            onClick={() => setEventDetailTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="event-drawer-content">
        {eventDetailTab === 'overview' && (
          <>
            <div className="event-metrics">
              <div>
                <small>耗时</small>
                <b>{formatDuration(selectedEvent.latency_ms)}</b>
              </div>
              <div>
                <small>状态码</small>
                <b>{selectedEvent.status_code ?? '—'}</b>
              </div>
              <div>
                <small>Token 总量</small>
                <TokenUsage event={selectedEvent} />
              </div>
            </div>
            <div className="event-info-list">
              {[
                ['请求 ID', selectedEvent.request_id],
                ['逻辑模型', selectedEvent.alias],
                ['部署', selectedEvent.deployment_name],
                ['供应商', selectedEvent.supplier_name],
                ['协议', selectedEvent.protocol],
                ['发生时间', fmtTime(selectedEvent.created_at)],
                ['结果', selectedEvent.status],
              ].map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <code>{value ?? '—'}</code>
                </div>
              ))}
            </div>
            <p className="field-hint">仅展示可用的脱敏路由记录，不包含请求正文或 API Token。</p>
          </>
        )}
        {eventDetailTab === 'attempts' && (
          <>
            {selectedEvent.attempts?.length ? (
              selectedEvent.attempts.map((attempt, index) => (
                <section className="attempt-card" key={index}>
                  <div>
                    <b>尝试 {index + 1}</b>
                    <span className={`badge ${attempt.status === 'error' ? 'red' : 'green'}`}>
                      {attempt.status}
                    </span>
                  </div>
                  <div className="event-info-list">
                    {[
                      ['部署', attempt.deployment],
                      ['供应商', attempt.supplier_name],
                      ['凭据序号', attempt.credential],
                      ['状态码', attempt.status],
                      ['错误类型', attempt.error_kind],
                    ]
                      .filter(([, v]) => v != null)
                      .map(([label, value]) => (
                        <div key={label}>
                          <span>{label}</span>
                          <code>{String(value)}</code>
                        </div>
                      ))}
                  </div>
                </section>
              ))
            ) : (
              <div className="event-empty">没有记录路由尝试信息</div>
            )}
          </>
        )}
        {eventDetailTab === 'error' && (
          <>
            {selectedEvent.error_message || selectedEvent.error_kind ? (
              <>
                <div className="event-tab-heading">
                  <div className="event-error-kind">{selectedEvent.error_kind || '错误详情'}</div>
                  <button
                    className="button secondary small"
                    onClick={() => copy(selectedEvent.error_message || selectedEvent.error_kind)}
                  >
                    <Copy size={14} />
                    复制错误信息
                  </button>
                </div>
                <pre className="event-error-message">
                  {selectedEvent.error_message || '无错误摘要'}
                </pre>
              </>
            ) : (
              <div className="event-empty">此请求没有错误信息</div>
            )}
          </>
        )}
        {eventDetailTab === 'raw' && (
          <>
            <div className="event-tab-heading">
              <span>完整事件数据</span>
              <button
                className="button secondary small"
                onClick={() => copy(JSON.stringify(selectedEvent, null, 2))}
              >
                <Copy size={14} />
                复制原始记录
              </button>
            </div>
            <pre className="trace-json event-raw-json">
              {JSON.stringify(selectedEvent, null, 2)}
            </pre>
          </>
        )}
      </div>
      <div className="event-drawer-footer">
        <span className="field-hint">不包含请求正文或 API Token</span>
        <button className="button primary" onClick={() => copy(selectedEvent.request_id)}>
          <Copy size={14} />
          复制请求 ID
        </button>
      </div>
    </Drawer>
  )
}
