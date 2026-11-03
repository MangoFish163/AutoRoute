export async function withMinimumDuration(task, duration = 1500) {
  const [result] = await Promise.allSettled([
    Promise.resolve().then(task),
    new Promise(resolve => setTimeout(resolve, duration)),
  ])
  if (result.status === 'rejected') throw result.reason
  return result.value
}
