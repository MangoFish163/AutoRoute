// Parse SSE across arbitrary network chunks, including split CRLF boundaries.
export async function consumeEvents(body, onNew) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let pending = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) return
      pending += decoder.decode(value, { stream: true })
      let boundary
      while ((boundary = /\r?\n\r?\n/.exec(pending))) {
        const frame = pending.slice(0, boundary.index)
        pending = pending.slice(boundary.index + boundary[0].length)
        if (frame.split(/\r?\n/).some((line) => /^event: *new$/.test(line))) onNew()
      }
    }
  } finally {
    try {
      await reader.cancel()
    } finally {
      reader.releaseLock()
    }
  }
}
