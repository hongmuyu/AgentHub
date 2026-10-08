import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  fetchAgentHubAgents, registerAgentHubAgent, updateAgentHubAgent,
  setAgentHubAgentEnabled
} from '../src/utils/apiFunctions.js'

const metadata = {
  name: 'Research Agent', description: 'Summarizes research',
  capabilities: ['summarize research'], tools: [], tags: ['research'],
  runtime_ref: 'workflow://research-agent/1'
}

test('Registry locales expose the admin action and error labels', () => {
  for (const language of ['en', 'zh']) {
    const messages = JSON.parse(readFileSync(new URL(`../src/locales/${language}.json`, import.meta.url)))
    assert.equal(typeof messages.nav.registry, 'string')
    for (const key of ['title', 'empty', 'register', 'update', 'enable', 'disable',
      'list_error', 'action_error', 'invalid_metadata', 'runtime_ref_hint']) {
      assert.equal(typeof messages.agenthub_registry[key], 'string', `${language}.${key}`)
    }
  }
})

test('list includes disabled Agents and supports every API page', async (t) => {
  const requests = []
  const response = { ok: true, json: async () => ({ agents: [], limit: 100, offset: 100 }) }
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return response
  })
  assert.equal(await fetchAgentHubAgents(100), response)
  assert.deepEqual(requests, [{
    url: '/api/agenthub/agents?include_disabled=true&limit=100&offset=100', options: undefined
  }])
})

test('register and update send only API metadata fields', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return { ok: true }
  })
  await registerAgentHubAgent(metadata)
  await updateAgentHubAgent('agent/1', metadata)
  assert.deepEqual(requests.map(({ url, options }) => [url, options.method]), [
    ['/api/agenthub/agents', 'POST'], ['/api/agenthub/agents/agent%2F1', 'PUT']
  ])
  for (const { options } of requests) {
    assert.equal(options.headers['Content-Type'], 'application/json')
    assert.deepEqual(JSON.parse(options.body), metadata)
  }
})

test('enable and disable use existing action endpoints without body', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return { ok: true }
  })
  await setAgentHubAgentEnabled('agent-1', false)
  await setAgentHubAgentEnabled('agent-1', true)
  assert.deepEqual(requests.map(({ url, options }) => [url, options]), [
    ['/api/agenthub/agents/agent-1/disable', { method: 'POST' }],
    ['/api/agenthub/agents/agent-1/enable', { method: 'POST' }]
  ])
})

test('validation, enable, and API failures stay visible to the view', async (t) => {
  const failures = [
    { ok: false, status: 422, json: async () => ({ detail: { code: 'INVALID_AGENT_METADATA' } }) },
    { ok: false, status: 409, json: async () => ({ detail: { code: 'INDEX_NOT_READY' } }) },
    { ok: false, status: 503, json: async () => ({ detail: { code: 'REGISTRY_READ_FAILED' } }) }
  ]
  t.mock.method(globalThis, 'fetch', async () => failures.shift())
  assert.equal((await registerAgentHubAgent(metadata)).status, 422)
  assert.equal((await setAgentHubAgentEnabled('agent-1', true)).status, 409)
  assert.equal((await fetchAgentHubAgents()).status, 503)
})
