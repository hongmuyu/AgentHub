import test, { before } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createSSRApp, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'

import { submitLaunchRequest } from '../src/utils/apiFunctions.js'

const messages = {
  en: JSON.parse(readFileSync(new URL('../src/locales/en.json', import.meta.url)))
}
const ready = {
  mode: 'agenthub', socket: {}, isConnectionReady: true,
  sessionId: 'session-1', taskPrompt: 'Review the report', attachmentIds: []
}
const candidateA = { id: 'agent-a', version: 1, name: 'Research Agent', raw_similarity: 0.91, score_kind: 'cosine' }
const candidateB = { id: 'agent-b', version: 2, name: 'Document Agent', raw_similarity: 0.76, score_kind: 'cosine' }
let component

before(async () => {
  try {
    const source = readFileSync(new URL('../src/components/AgentHubRoutingSummary.vue', import.meta.url), 'utf8')
    const { descriptor } = parse(source)
    const code = compileScript(descriptor, { id: 'routing-summary-test', inlineTemplate: true }).content
      .replaceAll('from "vue"', `from "${import.meta.resolve('vue')}"`)
      .replaceAll("from 'vue'", `from '${import.meta.resolve('vue')}'`)
      .replaceAll('from "vue-i18n"', `from "${import.meta.resolve('vue-i18n')}"`)
      .replaceAll("from 'vue-i18n'", `from '${import.meta.resolve('vue-i18n')}'`)
    component = (await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)).default
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
    component = null
  }
})

async function submitAndRender(t, body) {
  assert.ok(component, 'AgentHub routing summary component must load')
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, json: async () => body }))
  const response = await submitLaunchRequest(ready)
  const summary = await response.json()
  const app = createSSRApp({ render: () => h(component, { summary }) })
  app.use(createI18n({ legacy: false, locale: 'en', messages }))
  return renderToString(app)
}

test('selected route keeps recall order and separates rerank positions from raw cosine', async (t) => {
  const html = await submitAndRender(t, {
    routing_status: 'selected', selected_agent: { id: 'agent-b', version: 2, name: 'Document Agent' },
    candidates: [candidateA, candidateB],
    rerank_order: [
      { agent_id: 'agent-b', version: 2 }, { agent_id: 'agent-a', version: 1 }
    ]
  })
  const recall = html.match(/<ol class="routing-candidates">([\s\S]*?)<\/ol>/)?.[1] || ''
  const rerank = html.match(/<ol class="routing-rerank">([\s\S]*?)<\/ol>/)?.[1] || ''
  assert.ok(recall.indexOf('Research Agent') < recall.indexOf('Document Agent'))
  assert.ok(rerank.indexOf('Document Agent') < rerank.indexOf('Research Agent'))
  assert.match(html, /Raw cosine/)
  assert.match(html, /0\.91/)
  assert.match(html, /Selected Agent/)
  assert.match(html, /agent-b/)
  assert.match(html, /v2/)
  assert.doesNotMatch(html, /%|probability|confidence/i)
})

test('low-score rejection gives NO_SUITABLE_AGENT without a selected Agent', async (t) => {
  const html = await submitAndRender(t, {
    routing_status: 'rejected', selected_agent: { id: 'agent-a', version: 1, name: 'stale' },
    candidates: [{ ...candidateA, raw_similarity: 0.21 }],
    error_code: 'NO_SUITABLE_AGENT', rerank_order: null
  })
  assert.match(html, /No suitable Agent/)
  assert.match(html, /NO_SUITABLE_AGENT/)
  assert.match(html, /0\.21/)
  assert.doesNotMatch(html, /Selected Agent|stale/)
})

test('empty rejection renders an empty candidate explanation', async (t) => {
  const html = await submitAndRender(t, {
    routing_status: 'rejected', candidates: [], error_code: 'NO_SUITABLE_AGENT'
  })
  assert.match(html, /No candidates/)
  assert.doesNotMatch(html, /Selected Agent/)
})

test('routing infrastructure failure stays distinct from rejection', async (t) => {
  const html = await submitAndRender(t, {
    routing_status: 'failed', candidates: [], error_code: 'ROUTING_INFRASTRUCTURE_ERROR'
  })
  assert.match(html, /Routing failed/)
  assert.match(html, /ROUTING_INFRASTRUCTURE_ERROR/)
  assert.doesNotMatch(html, /No suitable Agent|Selected Agent/)
})

test('missing public fields render safe fallbacks', async (t) => {
  const html = await submitAndRender(t, {
    routing_status: 'selected', selected_agent: { id: 'agent-a', version: 1 },
    candidates: [{ id: 'agent-a', version: 1 }],
    rerank_order: [
      { agent_id: 'missing', version: 1 }, { agent_id: 'agent-a', version: 1 }
    ]
  })
  assert.match(html, /Agent agent-a/)
  assert.match(html, /Score unavailable/)
  assert.match(html, /Rerank order unavailable/)
  assert.doesNotMatch(html, /undefined|NaN|%/)
})

test('selected status without a valid selected Agent shows an unavailable decision', async (t) => {
  const html = await submitAndRender(t, {
    routing_status: 'selected', selected_agent: { id: 'agent-a' },
    candidates: [candidateA]
  })
  assert.match(html, /Selected Agent unavailable/)
  assert.doesNotMatch(html, /Selected Agent:/)
})
