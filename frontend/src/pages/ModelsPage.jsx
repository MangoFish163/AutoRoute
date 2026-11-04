import { useState } from 'react'

import { Plus, RefreshCw, Search, Sparkles } from 'lucide-react'

import { Button } from '../components/ui/button'

import { ModelRow, Empty } from '../shared'

export default function ModelsPage({
  aliases,
  deleteDeployment,
  deps,
  manualRefresh,
  openCreate,
  openEdit,
  refreshing,
  setSelectedModel,
  setTab,
}) {
  const [query, setQuery] = useState('')
  const filtered = deps.filter((d) =>
    `${d.name} ${d.alias} ${d.provider} ${d.upstream_model}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  )
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">CONFIGURATION</div>
          <h1>模型路由</h1>
          <p>管理供应商部署、上游凭据和路由权重。</p>
        </div>
        <Button variant="primary" onClick={openCreate}>
          <Plus size={16} />
          添加模型
        </Button>
      </div>
      <div className="toolbar">
        <div className="searchbox">
          <Search size={16} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索模型、供应商或别名..."
          />
        </div>
        <button className="button secondary" onClick={manualRefresh} disabled={refreshing}>
          {refreshing ? <RefreshCw className="spin" size={15} /> : <RefreshCw size={15} />}刷新
        </button>
        <button className="button secondary" onClick={() => setTab('tuning')}>
          <Sparkles size={15} />
          调优规则
        </button>
      </div>
      <div className="panel full-table">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>模型部署</th>
                <th>逻辑模型</th>
                <th>供应商</th>
                <th>状态</th>
                <th>生效权重</th>
                <th>Token</th>
                <th>RPM</th>
                <th>代理</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((d) => (
                <ModelRow
                  key={d.id}
                  dep={d}
                  expanded
                  onSelect={() => {
                    setSelectedModel(d)
                  }}
                  onEdit={() => openEdit(d)}
                  onDelete={() => deleteDeployment(d)}
                />
              ))}
              {!filtered.length && (
                <tr>
                  <td colSpan="9">
                    <Empty
                      title={deps.length ? '没有匹配项' : '开始配置你的模型'}
                      body={deps.length ? '试试其他搜索词。' : '添加供应商、模型 ID 与 API Token。'}
                      action={openCreate}
                    />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="panel-footer">
          <span>
            {filtered.length} 个部署 · {aliases.length} 个逻辑模型
          </span>
          <span className="muted">进入页面或模型变更后刷新</span>
        </div>
      </div>
    </>
  )
}
