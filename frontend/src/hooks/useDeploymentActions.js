import { useState } from 'react'
import { api } from '../lib/managementApi'

export function useDeploymentActions({ refresh, setDeps, setToast }) {
  const [selectedModel, setSelectedModel] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const deleteDeployment = (dep) => setDeleteTarget(dep)
  const confirmDelete = async () => {
    const dep = deleteTarget
    try {
      await api(`/deployments/${dep.id}`, { method: 'DELETE' })
      setDeleteTarget(null)
      setToast('已删除模型部署')
      refresh()
    } catch (e) {
      setToast(e.message, 'error')
    }
  }
  const toggleDeployment = async (dep) => {
    const enabled = !dep.enabled
    try {
      const updated = await api(`/deployments/${dep.id}/enabled`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      })
      setSelectedModel(updated)
      setDeps((old) => old.map((item) => (item.id === updated.id ? updated : item)))
      setToast(enabled ? '模型已启用' : '模型已停用')
    } catch (e) {
      setToast(e.message, 'error')
    }
  }
  return {
    selectedModel,
    setSelectedModel,
    deleteTarget,
    setDeleteTarget,
    deleteDeployment,
    confirmDelete,
    toggleDeployment,
  }
}
