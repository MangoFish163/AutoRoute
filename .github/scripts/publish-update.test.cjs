const test = require('node:test')
const assert = require('node:assert/strict')
const { requireNewer, publish } = require('./publish-update.cjs')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

test('feeds advance numerically and reject duplicate versions or downgrades', () => {
  requireNewer('0.1.9', '0.1.10')
  requireNewer('0.9.9', '1.0.0')
  for (const next of ['0.1.9', '0.1.8', '0.0.10', 'invalid']) {
    assert.throws(() => requireNewer('0.1.9', next))
  }
})

test('preflight performs no mutations when a feed does not yet exist', async () => {
  await publish({ context: { repo: { owner: 'test', repo: 'test' } }, preflight: true,
    github: { rest: { git: { getRef: async () => { throw Object.assign(new Error('missing'), { status: 404 }) } } } } })
})

test('API authorization failures are never treated as a missing feed', async () => {
  await assert.rejects(publish({ context: { repo: {} }, preflight: true,
    github: { rest: { git: { getRef: async () => { throw Object.assign(new Error('denied'), { status: 403 }) } } } } }), /denied/)
})

test('a feed switch creates a child commit and never force-overwrites a competing publisher', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'router-feed-'))
  const original = process.cwd()
  const calls = []
  try {
    fs.mkdirSync(path.join(directory, 'frontend'))
    fs.mkdirSync(path.join(directory, 'release'))
    fs.writeFileSync(path.join(directory, 'frontend/package.json'), JSON.stringify({ version: '0.1.2' }))
    fs.writeFileSync(path.join(directory, 'release/preview.json'), JSON.stringify({ version: '0.1.2' }))
    process.chdir(directory)
    const git = {
      getRef: async () => ({ data: { object: { sha: 'parent' } } }),
      getCommit: async () => ({ data: { tree: { sha: 'existing-tree' } } }),
      createTree: async args => { calls.push(args); return { data: { sha: 'new-tree' } } },
      createCommit: async args => { calls.push(args); return { data: { sha: 'new-commit' } } },
      updateRef: async args => { calls.push(args); throw Object.assign(new Error('competing commit'), { status: 422 }) },
    }
    const repos = { getContent: async () => ({ data: { content: Buffer.from('{"version":"0.1.1"}').toString('base64') } }) }
    await assert.rejects(publish({ github: { rest: { git, repos } }, context: { repo: { owner: 'test', repo: 'repo' } } }), /competing commit/)
    assert.equal(calls[0].base_tree, 'existing-tree')
    assert.deepEqual(calls[1].parents, ['parent'])
    assert.equal(calls[2].force, false)
    assert.equal(calls[2].ref, 'heads/updates')
  } finally {
    process.chdir(original)
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    assert.ok(path.basename(directory).startsWith('router-feed-'))
    fs.rmSync(directory, { recursive: true, force: true })
  }
})
