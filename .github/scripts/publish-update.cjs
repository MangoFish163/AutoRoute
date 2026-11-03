const fs = require('node:fs')

function versionParts(version) {
  if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error('Invalid updater version')
  return version.split('.').map(BigInt)
}
function requireNewer(previous, next) {
  const a = versionParts(previous), b = versionParts(next)
  for (let i = 0; i < 3; i++) {
    if (b[i] > a[i]) return
    if (b[i] < a[i]) break
  }
  throw new Error(`Update feed already contains ${previous}; increment the application version before publishing ${next}.`)
}

async function publish({ github, context, channel = 'preview', preflight = false }) {
  if (!['preview', 'stable'].includes(channel)) throw new Error('Unknown update channel')
  const repo = context.repo
  const version = JSON.parse(fs.readFileSync('frontend/package.json', 'utf8')).version
  versionParts(version)
  let parent, tree
  try {
    const ref = await github.rest.git.getRef({ ...repo, ref: 'heads/updates' })
    parent = ref.data.object.sha
    const commit = await github.rest.git.getCommit({ ...repo, commit_sha: parent })
    tree = commit.data.tree.sha
  } catch (error) { if (error.status !== 404) throw error }
  if (parent) {
    try {
      const previous = await github.rest.repos.getContent({ ...repo, path: `${channel}.json`, ref: parent })
      const data = JSON.parse(Buffer.from(previous.data.content, 'base64').toString('utf8'))
      requireNewer(data.version, version)
    } catch (error) { if (error.status !== 404) throw error }
  }
  if (preflight) return
  const content = fs.readFileSync(`release/${channel}.json`, 'utf8')
  if (JSON.parse(content).version !== version) throw new Error('Update manifest version differs from this build')
  const created = await github.rest.git.createTree({ ...repo, ...(tree ? { base_tree: tree } : {}),
    tree: [{ path: `${channel}.json`, mode: '100644', type: 'blob', content }] })
  const commit = await github.rest.git.createCommit({ ...repo, message: `chore(updater): 发布 ${channel} ${version}`,
    tree: created.data.sha, parents: parent ? [parent] : [] })
  // Atomic, non-forced ref update. A failed publish leaves the previous feed
  // intact. The branch contains only manifests and never application secrets.
  if (parent) await github.rest.git.updateRef({ ...repo, ref: 'heads/updates', sha: commit.data.sha, force: false })
  else await github.rest.git.createRef({ ...repo, ref: 'refs/heads/updates', sha: commit.data.sha })
}
module.exports = { publish, requireNewer }
