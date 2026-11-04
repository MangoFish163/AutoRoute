import { useEffect, useRef, useState } from 'react'
import { todayRange } from '../shared'

export function useLogFilters() {
  const [logQuery, setLogQuery] = useState('')
  const [logRange, setLogRange] = useState(todayRange)
  const [logStart, logEnd] = logRange
  const [logPage, setLogPage] = useState(1)
  const [logsLive, setLogsLive] = useState(false)
  const logRangeManuallyChanged = useRef(false)
  const [logStatusOpen, setLogStatusOpen] = useState(false)
  useEffect(() => {
    const refreshRange = () => {
      if (!logRangeManuallyChanged.current) setLogRange(todayRange())
    }
    document.addEventListener('visibilitychange', refreshRange)
    return () => document.removeEventListener('visibilitychange', refreshRange)
  }, [])
  useEffect(() => {
    const timer = setInterval(() => {
      if (logRangeManuallyChanged.current) return
      const [start, end] = todayRange()
      setLogRange([start, end])
      setLogPage(1)
    }, 60000)
    return () => clearInterval(timer)
  }, [])

  return {
    logQuery,
    setLogQuery,
    logStart,
    logEnd,
    logPage,
    setLogPage,
    logsLive,
    setLogsLive,
    logRangeManuallyChanged,
    logStatusOpen,
    setLogStatusOpen,
    setLogRange,
  }
}
