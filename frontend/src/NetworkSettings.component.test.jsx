import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import NetworkSettings from './NetworkSettings'
import { api } from './lib/managementApi'

vi.mock('./lib/managementApi', () => ({ api: vi.fn() }))

beforeEach(() => {
  api.mockReset()
  api.mockImplementation(async (path) =>
    path === '/network/recommendations' ? { ports: [39999, 38787, 9999] } : { port: 8787 },
  )
})

const writes = () => api.mock.calls.filter(([, options]) => options?.method === 'PUT')

it('allows uninterrupted typing and caret editing, then saves only on blur', async () => {
  const user = userEvent.setup()
  render(<NetworkSettings />)
  const input = screen.getByRole('textbox', { name: '下次启动端口' })
  await waitFor(() => expect(input).toHaveValue('8787'))
  expect(writes()).toHaveLength(0)
  await user.clear(input)
  await user.type(input, '39999', { delay: 20 })
  expect(input).toHaveValue('39999')
  expect(input.selectionStart).toBe(input.selectionEnd)
  await user.keyboard('{ArrowLeft}{Backspace}8')
  expect(input).toHaveValue('39989')
  expect(writes()).toHaveLength(0)
  await user.tab()
  await waitFor(() => expect(writes()).toHaveLength(1))
  expect(JSON.parse(writes()[0][1].body)).toEqual({ port: 39989 })
})

it('saves the clicked recommendation instead of the unfinished input', async () => {
  const user = userEvent.setup()
  render(<NetworkSettings />)
  const input = screen.getByRole('textbox', { name: '下次启动端口' })
  await waitFor(() => expect(input).toHaveValue('8787'))
  await user.clear(input)
  await user.type(input, '1234')
  await user.click(screen.getByRole('button', { name: '39999' }))
  expect(input).toHaveValue('39999')
  expect(writes()).toHaveLength(1)
  expect(JSON.parse(writes()[0][1].body)).toEqual({ port: 39999 })
})

it('validates incomplete ports after blur without sending a request', async () => {
  const user = userEvent.setup()
  render(<NetworkSettings />)
  const input = screen.getByRole('textbox', { name: '下次启动端口' })
  await waitFor(() => expect(input).toHaveValue('8787'))
  await user.clear(input)
  await user.type(input, '99')
  await user.tab()
  expect(screen.getByRole('status')).toHaveTextContent('请输入 1024～65535 的整数端口')
  expect(writes()).toHaveLength(0)
})
