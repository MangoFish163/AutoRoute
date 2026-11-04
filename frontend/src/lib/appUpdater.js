// Transport is injected so failed verification, retries and concurrent clicks can
// be checked without launching an installer or stopping the local gateway.
export function createUpdateController(transport, channel = 'preview') {
  let snapshot = {
    phase: 'idle',
    channel,
    update: null,
    error: '',
    retry: 'check',
    downloaded: 0,
    total: null,
  }
  const listeners = new Set()
  const publish = (patch) => {
    snapshot = { ...snapshot, ...patch }
    listeners.forEach((listener) => listener())
  }
  let busy = false
  const run = async (phase, retry, action) => {
    if (busy) return
    busy = true
    publish({ phase, error: '', retry })
    try {
      await action()
    } catch (error) {
      publish({ phase: 'error', error: String(error) })
    } finally {
      busy = false
    }
  }
  const check = (channel = snapshot.channel) =>
    run('checking', 'check', async () => {
      publish({ channel, update: null, downloaded: 0, total: null })
      const update = await transport.check(channel)
      publish({ update, phase: update ? 'available' : 'current' })
    })
  const download = () => {
    if (!snapshot.update) return
    return run('downloading', 'download', async () => {
      publish({ downloaded: 0, total: null })
      await transport.download((event) => {
        publish({ downloaded: event.downloaded, total: event.total })
      })
      // The native download call returns only after signature verification.
      publish({ phase: 'ready' })
    })
  }
  const install = () => {
    if (snapshot.phase !== 'ready' && !(snapshot.phase === 'error' && snapshot.retry === 'install'))
      return
    return run('installing', 'install', async () => {
      await transport.install()
      // On Windows success exits this process. An unexpected return must not
      // leave the user stuck behind an installation overlay.
      throw new Error('安装程序未能接管更新，请重试。')
    })
  }
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    check,
    download,
    install,
    retry: () =>
      snapshot.retry === 'install'
        ? install()
        : snapshot.retry === 'download'
          ? download()
          : check(),
  }
}
