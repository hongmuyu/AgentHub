import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import * as apiFunctions from '../src/utils/apiFunctions.js'

const {
  postFile, submitLaunchRequest, fetchAgentHubRun, fetchAgentHubMetrics,
  fetchWorkflowsWithDesc, fetchWorkflowYAML, getAttachment, fetchLogsZip
} = apiFunctions


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


test('manual Launch uses the same uploaded attachment and WebSocket session', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return url.startsWith('/api/uploads/')
      ? { ok: true, json: async () => ({ attachment_id: 'legacy-upload', name: 'notes.txt' }) }
      : { ok: true, json: async () => ({ status: 'started', session_id: 'session-123' }) }
  })

  const uploaded = await postFile('session-123', new Blob(['notes']))
  const response = await submitLaunchRequest({
    ...ready, mode: 'manual', yamlFile: 'manual.yaml',
    attachmentIds: [uploaded.attachmentId]
  })

  assert.equal(uploaded.success, true)
  assert.equal(response.ok, true)
  assert.deepEqual(requests.map(({ url }) => url), [
    '/api/uploads/session-123', '/api/workflow/execute'
  ])
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    yaml_file: 'manual.yaml', task_prompt: 'Summarize the report',
    session_id: 'session-123', attachments: ['legacy-upload']
  })
})


test('Registry navigation leaves original Launch and Workflow entry paths available', () => {
  const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
  const sidebar = readFileSync(new URL('../src/components/Sidebar.vue', import.meta.url), 'utf8')
  assert.match(router, /path: '\/launch',[\s\S]*?import\('\.\.\/pages\/LaunchView\.vue'\)/)
  assert.match(router, /path: '\/workflows\/:name\?',[\s\S]*?import\('\.\.\/pages\/WorkflowWorkbench\.vue'\)/)
  assert.match(router, /path: '\/agenthub\/registry',[\s\S]*?import\('\.\.\/pages\/AgentHubRegistryView\.vue'\)/)
  for (const path of ['/launch', '/workflows', '/agenthub/registry']) {
    assert.ok(sidebar.includes(`to="${path}"`))
  }
})


test('Workflow page helpers retain legacy YAML list and content requests', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url) => {
    requests.push(url)
    if (url === '/api/workflows') {
      return { ok: true, json: async () => ({ workflows: ['manual.yaml'] }) }
    }
    if (url.endsWith('/desc')) {
      return { ok: true, json: async () => ({ description: 'Manual workflow' }) }
    }
    return { ok: true, json: async () => ({ content: 'name: manual\n' }) }
  })

  assert.deepEqual(await fetchWorkflowsWithDesc(), {
    success: true, workflows: [{ name: 'manual.yaml', description: 'Manual workflow' }]
  })
  assert.equal(await fetchWorkflowYAML('manual.yaml'), 'name: manual\n')
  assert.deepEqual(requests, [
    '/api/workflows', '/api/workflows/manual.yaml/desc', '/api/workflows/manual.yaml/get'
  ])
})


test('legacy artifact and session download helpers retain session routes', async (t) => {
  const requests = []
  const downloads = []
  t.mock.method(globalThis, 'fetch', async (url) => {
    requests.push(url)
    return url.includes('/artifacts/')
      ? { ok: true, json: async () => ({ data_uri: 'data:text/plain;base64,b2s=' }) }
      : { ok: true, blob: async () => new Blob(['zip']) }
  })
  const previousWindow = globalThis.window
  const previousDocument = globalThis.document
  t.after(() => {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
    if (previousDocument === undefined) delete globalThis.document
    else globalThis.document = previousDocument
  })
  globalThis.window = {
    URL: { createObjectURL: () => 'blob:legacy', revokeObjectURL: (url) => downloads.push(url) }
  }
  globalThis.document = {
    createElement: () => ({ click() { downloads.push('clicked') } }),
    body: { appendChild() {}, removeChild() {} }
  }

  assert.equal(await getAttachment('session-123', 'artifact-1'), 'data:text/plain;base64,b2s=')
  assert.deepEqual(await fetchLogsZip('session-123'), { success: true })
  assert.deepEqual(requests, [
    '/api/sessions/session-123/artifacts/artifact-1',
    '/api/sessions/session-123/download'
  ])
  assert.deepEqual(downloads, ['clicked', 'blob:legacy'])
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

test('AgentHub run query encodes run_id and preserves HTTP failure', async (t) => {
  const response = { ok: false, status: 503 }
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => response)
  assert.equal(await fetchAgentHubRun('run/one'), response)
  assert.equal(fetchMock.mock.calls[0].arguments[0], '/api/agenthub/tasks/run%2Fone')
  assert.equal(fetchMock.mock.calls[0].arguments[1], undefined)
})

test('AgentHub metrics read uses the existing API origin and preserves failure', async (t) => {
  const response = { ok: false, status: 503 }
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => response)
  assert.equal(await fetchAgentHubMetrics(), response)
  assert.equal(fetchMock.mock.calls[0].arguments[0], '/api/agenthub/metrics')
  assert.equal(fetchMock.mock.calls[0].arguments[1], undefined)
})
