import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/managementApi'

export function useHistory({ settings, setToast }) {
  const [historyDrawer, updateDrawer] = useState(null)
  const [historyData, setHistoryData] = useState({
    weights: { items: [], total: 0 },
    audits: { items: [], total: 0 },
  })
  const [historyLoading, setHistoryLoading] = useState(false)
  const [expandedHistoryId, setExpandedHistoryId] = useState(null)
  const request = useRef(null)
  useEffect(() => () => request.current?.abort(), [])

  const setHistoryDrawer = (kind) => {
    request.current?.abort()
    request.current = null
    updateDrawer(kind)
    setHistoryLoading(false)
  }
  const fetchHistory = async (kind, offset, append) => {
    const controller = new AbortController()
    request.current = controller
    setHistoryLoading(true)
    const active = () => request.current === controller && !controller.signal.aborted
    try {
      const data = await api(`/history/${kind}?limit=20&offset=${offset}`, {
        signal: controller.signal,
      })
      if (!active()) return
      setHistoryData((old) => ({
        ...old,
        [kind]: { ...data, items: append ? [...old[kind].items, ...data.items] : data.items },
      }))
    } catch (e) {
      if (!active()) return
      if (!append && (e.message === 'Not Found' || e.message.includes('404'))) {
        const items = kind === 'weights' ? settings?.weight_events || [] : settings?.ai_audits || []
        setHistoryData((old) => ({
          ...old,
          [kind]: { items, total: items.length, limit: items.length, offset: 0 },
        }))
      } else if (e.name !== 'AbortError') setToast(e.message, 'error')
    } finally {
      if (active()) {
        request.current = null
        setHistoryLoading(false)
      }
    }
  }
  const openHistory = (kind) => {
    setHistoryDrawer(kind)
    setExpandedHistoryId(null)
    return fetchHistory(kind, 0, false)
  }
  const loadMoreHistory = () => {
    if (!historyDrawer || request.current) return
    return fetchHistory(historyDrawer, historyData[historyDrawer].items.length, true)
  }
  return {
    historyDrawer,
    setHistoryDrawer,
    historyData,
    historyLoading,
    expandedHistoryId,
    setExpandedHistoryId,
    openHistory,
    loadMoreHistory,
  }
}
