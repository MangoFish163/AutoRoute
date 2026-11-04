import test from 'node:test'
import assert from 'node:assert/strict'

test('desktop requests get the current token over IPC and keep it out of URLs/storage', async () => {
  globalThis.isTauri = true
  let token = 'first-launch-token',
    received
  globalThis.window = {
    __TAURI_INTERNALS__: {
      invoke: async (command) => {
        if (command === 'backend_url') return 'http://127.0.0.1:19321'
        assert.equal(command, 'management_token')
        return token
      },
    },
  }
  globalThis.fetch = async (url, options) => {
    received = { url, options }
    return new Response('{}')
  }
  const { api, managementFetch } = await import('./managementApi.js?desktop')
  await api('/bootstrap', { headers: { 'X-Test': 'preserved', 'X-Management-Token': 'override' } })
  assert.equal(received.url, 'http://127.0.0.1:19321/api/bootstrap')
  assert.equal(received.options.headers.get('X-Management-Token'), token)
  assert.equal(received.options.headers.get('X-Test'), 'preserved')
  assert.equal(received.options.headers.get('Content-Type'), 'application/json')
  token = 'next-launch-token'
  const controller = new AbortController()
  await managementFetch('/events/stream?start=0&end=1', { signal: controller.signal })
  assert.equal(received.options.headers.get('X-Management-Token'), token)
  assert.equal(received.options.signal, controller.signal)
  assert.ok(!received.url.includes(token))
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: [{ msg: 'Input should be a valid number' }] }), {
      status: 422,
    })
  await assert.rejects(api('/deployments/test-connection'), /Input should be a valid number/)
})

test('browser development consumes and removes fragment, then authenticates relative API requests', async () => {
  globalThis.isTauri = false
  const storage = new Map()
  let replaced, received
  globalThis.window = {
    location: { hash: '#management_token=dev-token', pathname: '/', search: '' },
  }
  globalThis.sessionStorage = {
    getItem: (key) => storage.get(key),
    setItem: (key, value) => storage.set(key, value),
  }
  globalThis.history = {
    replaceState: (...args) => {
      replaced = args[2]
    },
  }
  globalThis.fetch = async (url, options) => {
    received = { url, options }
    return new Response('{}')
  }
  const { api } = await import('./managementApi.js?browser')
  assert.equal(replaced, '/')
  await api('/settings')
  assert.equal(received.url, '/api/settings')
  assert.equal(received.options.headers.get('X-Management-Token'), 'dev-token')
  storage.clear()
  await assert.rejects(api('/bootstrap'), /管理连接未初始化/)
})
