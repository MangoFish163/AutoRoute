import test from 'node:test'
import assert from 'node:assert/strict'
import { consumeEvents } from './serverEvents.js'

test('management SSE parses fragmented frames and ignores heartbeats/ready', async () => {
  const wire = new TextEncoder().encode('event: ready\r\ndata: {}\r\n\r\n: keep-alive\n\nevent: new\r\ndata: {"id":"中文"}\r\n\r\nevent: new\ndata: {}\n\n')
  for (const size of [1, 7, wire.length]) {
    let offset = 0, count = 0
    const body = new ReadableStream({ pull(controller) {
      if (offset >= wire.length) return controller.close()
      controller.enqueue(wire.slice(offset, offset += size))
    } })
    await consumeEvents(body, () => count++)
    assert.equal(count, 2)
    assert.equal(body.locked, false)
  }
})
