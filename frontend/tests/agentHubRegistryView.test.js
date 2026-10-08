import test, { before } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createSSRApp, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'

const messages = { en: JSON.parse(readFileSync(new URL('../src/locales/en.json', import.meta.url))) }
const id = '11111111-1111-4111-8111-111111111111'
const agent = {
  id, name: 'Research Agent', description: 'Summarizes research',
  capabilities: ['summarize research'], tools: [], tags: ['research'],
  runtime_ref: 'workflow://research-agent/1', version: 1, status: 'active'
}
const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
let component

before(async () => {
  const source = readFileSync(new URL('../src/pages/AgentHubRegistryView.vue', import.meta.url), 'utf8')
  const code = compileScript(parse(source).descriptor, { id: 'registry-state-test' }).content
    .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`)
    .replace("from 'vue-i18n'", `from '${import.meta.resolve('vue-i18n')}'`)
    .replace("from '../utils/apiFunctions.js'", `from '${new URL('../src/utils/apiFunctions.js', import.meta.url).href}'`)
  component = (await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)).default
})

async function createView() {
  let view
  const app = createSSRApp({
    setup() {
      view = component.setup({}, { expose() {} })
      return () => h('div')
    }
  })
  app.use(createI18n({ legacy: false, locale: 'en', messages }))
  await renderToString(app)
  return view
}

test('empty catalog is a successful empty list, not an API error', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => reply({ agents: [] }))
  const view = await createView()
  assert.equal(await view.loadAgents(), true)
  assert.deepEqual(view.agents.value, [])
  assert.equal(view.listError.value, '')
})

test('catalog loads beyond the API page limit', async (t) => {
  const offsets = []
  t.mock.method(globalThis, 'fetch', async (url) => {
    const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset'))
    offsets.push(offset)
    return reply({ agents: offset === 0
      ? Array.from({ length: 100 }, (_, index) => ({ ...agent, id: `agent-${index}` }))
      : [{ ...agent, id: 'agent-100' }] })
  })
  const view = await createView()
  assert.equal(await view.loadAgents(), true)
  assert.equal(view.agents.value.length, 101)
  assert.deepEqual(offsets, [0, 100])
})

test('register, edit metadata, disable, and enable refresh server state', async (t) => {
  const requests = []
  let stored = null
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    if (options?.method === 'POST' && url === '/api/agenthub/agents') {
      stored = { ...agent, ...JSON.parse(options.body) }
      return reply(stored, 201)
    }
    if (options?.method === 'PUT') {
      stored = { ...stored, ...JSON.parse(options.body), version: stored.version + 1 }
      return reply(stored)
    }
    if (url.endsWith('/disable')) {
      stored = { ...stored, status: 'disabled' }
      return reply(stored)
    }
    if (url.endsWith('/enable')) {
      stored = { ...stored, status: 'active' }
      return reply(stored)
    }
    return reply({ agents: stored ? [stored] : [] })
  })
  const view = await createView()
  await view.loadAgents()
  Object.assign(view.form, {
    name: 'Research Agent', description: 'Summarizes research',
    capabilities: 'summarize research\n', tools: '', tags: 'research',
    runtime_ref: 'workflow://research-agent/1'
  })
  await view.saveAgent()
  assert.equal(view.agents.value[0].name, 'Research Agent')
  assert.equal(view.agents.value[0].status, 'active')
  assert.equal(view.actionSuccess.value, messages.en.agenthub_registry.registered)
  assert.deepEqual(JSON.parse(requests.find((entry) => entry.options?.method === 'POST').options.body), {
    name: 'Research Agent', description: 'Summarizes research',
    capabilities: ['summarize research'], tools: [], tags: ['research'],
    runtime_ref: 'workflow://research-agent/1'
  })

  view.editAgent(view.agents.value[0])
  view.form.description = 'Updated research summary'
  await view.saveAgent()
  assert.equal(view.agents.value[0].description, 'Updated research summary')
  assert.equal(view.agents.value[0].version, 2)
  assert.equal(view.editingId.value, null)
  assert.equal(view.actionSuccess.value, messages.en.agenthub_registry.updated)

  await view.toggleAgent(view.agents.value[0])
  assert.equal(view.agents.value[0].status, 'disabled')
  assert.equal(view.actionSuccess.value, messages.en.agenthub_registry.disabled_action)
  await view.toggleAgent(view.agents.value[0])
  assert.equal(view.agents.value[0].status, 'active')
  assert.equal(view.actionSuccess.value, messages.en.agenthub_registry.enabled)
  assert.deepEqual(requests.filter((entry) => entry.url.endsWith('/enable') || entry.url.endsWith('/disable'))
    .map((entry) => entry.url.split('/').at(-1)), ['disable', 'enable'])
  assert.equal(requests.filter((entry) => entry.url.includes('include_disabled=true')).length, 5)
})

test('invalid metadata and runtime reference do not create success or alter the list', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return options?.method === 'POST'
      ? reply({ detail: { code: 'UNKNOWN_RUNTIME_REF', private: 'hidden' } }, 422)
      : reply({ agents: [] })
  })
  const view = await createView()
  await view.loadAgents()
  await view.saveAgent()
  assert.equal(view.actionError.value, 'INVALID_AGENT_METADATA')
  assert.equal(requests.length, 1)
  Object.assign(view.form, {
    name: 'Research Agent', description: 'Summarizes research',
    capabilities: 'summarize research', runtime_ref: 'workflow://unknown/1'
  })
  await view.saveAgent()
  assert.equal(view.actionError.value, 'UNKNOWN_RUNTIME_REF')
  assert.equal(view.actionSuccess.value, '')
  assert.deepEqual(view.agents.value, [])
  assert.equal(requests.length, 2)
})

test('failed enable leaves disabled status untouched for invalid runtime and index', async (t) => {
  let failure = 'UNKNOWN_RUNTIME_REF'
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    if (url.endsWith('/enable')) return reply({ detail: { code: failure } }, 409)
    return reply({ agents: [{ ...agent, status: 'disabled' }] })
  })
  const view = await createView()
  await view.loadAgents()
  for (const code of ['UNKNOWN_RUNTIME_REF', 'INDEX_NOT_READY']) {
    failure = code
    await view.toggleAgent(view.agents.value[0])
    assert.equal(view.actionError.value, code)
    assert.equal(view.actionSuccess.value, '')
    assert.equal(view.agents.value[0].status, 'disabled')
  }
  assert.equal(requests.filter((entry) => entry.url.includes('include_disabled=true')).length, 1)
})

test('list and action API errors expose safe codes only', async (t) => {
  let mode = 'list'
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (mode === 'list') return reply({ detail: { code: 'secret=raw-value' } }, 503)
    if (options?.method === 'POST') return reply({ detail: { code: 'REGISTRY_WRITE_FAILED', private: 'hidden' } }, 503)
    return reply({ agents: [agent] })
  })
  const view = await createView()
  assert.equal(await view.loadAgents(), false)
  assert.equal(view.listError.value, 'REGISTRY_READ_FAILED')
  assert.deepEqual(view.agents.value, [])
  mode = 'action'
  await view.loadAgents()
  await view.toggleAgent(view.agents.value[0])
  assert.equal(view.actionError.value, 'REGISTRY_WRITE_FAILED')
  assert.equal(view.agents.value[0].status, 'active')
})
