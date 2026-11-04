import { useCallback, useEffect, useRef, useState } from 'react'

export function useToasts() {
  const [queue, setQueue] = useState([])
  const id = useRef(0)
  const push = useCallback((message, type = 'success') => {
    if (typeof message === 'object') ({ message, type = 'success' } = message)
    setQueue((old) => [...old, { id: ++id.current, message, type }].slice(-5))
  }, [])
  const toast = queue[0]
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setQueue((old) => old.slice(1)), 3200)
    return () => clearTimeout(timer)
  }, [toast])
  return [toast, push]
}
