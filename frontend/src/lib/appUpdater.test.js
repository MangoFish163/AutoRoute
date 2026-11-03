import test from 'node:test'
import assert from 'node:assert/strict'
import { createUpdateController } from './appUpdater.js'

const update = { version: '0.1.2', notes: 'test' }
test('checking and downloading never install; installation requires a separate action', async () => {
  const calls = []
  const controller = createUpdateController({
    check: async channel => { calls.push(channel); return update },
    download: async progress => { progress({ downloaded: 42, total: null }); calls.push('download') },
    install: async () => { calls.push('install'); throw new Error('launch failed') },
  })
  await controller.install()
  assert.deepEqual(calls, [])
  await controller.check()
  assert.equal(controller.getSnapshot().phase, 'available')
  assert.deepEqual(calls, ['preview'])
  await controller.download()
  assert.equal(controller.getSnapshot().phase, 'ready')
  assert.equal(controller.getSnapshot().downloaded, 42)
  assert.deepEqual(calls, ['preview', 'download'])
  await controller.install()
  assert.equal(controller.getSnapshot().retry, 'install')
  assert.equal(controller.getSnapshot().phase, 'error')
  await controller.retry()
  assert.deepEqual(calls, ['preview', 'download', 'install', 'install'])
})

test('failed signature verification cannot enter ready or install; retry downloads again', async () => {
  let attempts = 0, installs = 0
  const controller = createUpdateController({
    check: async () => update,
    download: async () => { if (++attempts === 1) throw new Error('bad signature') },
    install: async () => { installs++ },
  })
  await controller.check()
  await controller.download()
  assert.equal(controller.getSnapshot().phase, 'error')
  await controller.install()
  assert.equal(installs, 0)
  await controller.retry()
  assert.equal(attempts, 2)
  assert.equal(controller.getSnapshot().phase, 'ready')
})

test('concurrent clicks and channel changes cannot replace an in-flight update', async () => {
  let complete, checks = 0
  const controller = createUpdateController({
    check: () => { checks++; return new Promise(resolve => { complete = resolve }) },
  })
  const pending = controller.check()
  await controller.check('stable')
  assert.equal(checks, 1)
  assert.equal(controller.getSnapshot().channel, 'preview')
  complete(update)
  await pending
  assert.equal(controller.getSnapshot().update.version, '0.1.2')
})

test('switching channels discards an already downloaded update, including on check failure', async () => {
  const controller = createUpdateController({
    check: async channel => { if (channel === 'stable') throw new Error('offline'); return update },
    download: async () => {},
    install: async () => assert.fail('Must not install stale preview bytes'),
  })
  await controller.check()
  await controller.download()
  await controller.check('stable')
  assert.equal(controller.getSnapshot().channel, 'stable')
  assert.equal(controller.getSnapshot().update, null)
  await controller.install()
  assert.equal(controller.getSnapshot().phase, 'error')
})

test('a channel without a newer release does not offer an update', async () => {
  const controller = createUpdateController({ check: async () => null })
  await controller.check('stable')
  assert.equal(controller.getSnapshot().phase, 'current')
})
