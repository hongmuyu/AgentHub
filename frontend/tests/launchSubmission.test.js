import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import * as apiFunctions from '../src/utils/apiFunctions.js'

const { postFile, submitLaunchRequest } = apiFunctions


const ready = {
  socket: { readyState: 1 },
  isConnectionReady: true,
  sessionId: 'session-123',
  taskPrompt: 'Summarize the report',
  attachmentIds: ['attachment-1']
}


test('both Launch locales define the AgentHub mode and pending state labels', () => {
  for (const language of ['en', 'zh']) {
    const messages = JSON.parse(readFileSync(new URL(`../src/locales/${language}.json`, import.meta.url)))
    for (const key of ['launch_mode', 'manual_mode', 'agenthub_mode', 'routing_strategy',
      'agenthub_request_accepted', 'agenthub_cancel_requested',
      'agenthub_workflow_completed']) {
      assert.equal(typeof messages.launch[key], 'string', `${language}.launch.${key}`)
    }
  }
})


test('manual YAML launch keeps the original endpoint and body', async (t) => {
  assert.equal(typeof submitLaunchRequest, 'function')
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return { ok: true }
  })

  await submitLaunchRequest({ ...ready, mode: 'manual', yamlFile: 'review.yaml' })

  assert.equal(requests.length, 1)
  assert.equal(requests[0].url, '/api/workflow/execute')
  assert.equal(requests[0].options.method, 'POST')
  assert.equal(requests[0].options.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    yaml_file: 'review.yaml', task_prompt: 'Summarize the report',
    session_id: 'session-123', attachments: ['attachment-1']
  })
})


test('AgentHub launch sends task and default semantic strategy without YAML', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return { ok: true }
  })

  await submitLaunchRequest({ ...ready, mode: 'agenthub', yamlFile: 'ignored.yaml' })

  assert.equal(requests.length, 1)
  assert.equal(requests[0].url, '/api/agenthub/tasks')
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    task: 'Summarize the report', session_id: 'session-123',
    attachments: ['attachment-1'], routing_strategy: 'semantic'
  })
})


test('AgentHub strategy and uploaded attachment ID belong to the same session', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return {
      ok: true,
      json: async () => ({ attachment_id: 'uploaded-7', name: 'report.txt' })
    }
  })

  const uploaded = await postFile('session-123', new Blob(['report']))
  await submitLaunchRequest({
    ...ready, mode: 'agenthub', attachmentIds: [uploaded.attachmentId],
    routingStrategy: 'semantic_llm'
  })

  assert.equal(requests[0].url, '/api/uploads/session-123')
  assert.equal(requests[0].options.body.get('file').size, 6)
  assert.equal(requests[1].url, '/api/agenthub/tasks')
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    task: 'Summarize the report', session_id: 'session-123',
    attachments: ['uploaded-7'], routing_strategy: 'semantic_llm'
  })
})


test('neither mode dispatches before the WebSocket session is ready', async (t) => {
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => ({ ok: true }))
  for (const mode of ['manual', 'agenthub']) {
    for (const changes of [
      { socket: null }, { isConnectionReady: false }, { sessionId: null }
    ]) {
      assert.equal(await submitLaunchRequest({ ...ready, ...changes, mode }), null)
    }
  }
  assert.equal(fetchMock.mock.callCount(), 0)
})


test('API rejection and transport failure remain failures', async (t) => {
  const response = { ok: false, status: 422, json: async () => ({ detail: { code: 'SESSION_NOT_FOUND' } }) }
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => response)
  assert.equal(await submitLaunchRequest({ ...ready, mode: 'agenthub' }), response)
  assert.equal(fetchMock.mock.callCount(), 1)
  fetchMock.mock.mockImplementation(async () => { throw new Error('offline') })
  await assert.rejects(submitLaunchRequest({ ...ready, mode: 'agenthub' }), /offline/)
})
