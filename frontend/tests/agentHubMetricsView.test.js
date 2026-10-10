import test, { before } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createSSRApp, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'

const messages = { en: JSON.parse(readFileSync(new URL('../src/locales/en.json', import.meta.url))) }
const empty = {
  total_runs: 0,
  run_status_counts: { pending: 0, running: 0, success: 0, failed: 0, rejected: 0, cancelled: 0 },
  execution_success_rate: { numerator: 0, denominator: 0, value: null },
  execution_failure_rate: { numerator: 0, denominator: 0, value: null },
  rejected_rate: { numerator: 0, denominator: 0, value: null },
  average_routing_latency_ms: { sample_count: 0, value: null },
  average_execution_latency_ms: { sample_count: 0, value: null },
  token_usage: {
    total: { sum: null, known_count: 0, unknown_count: 0 },
    input: { sum: null, known_count: 0, unknown_count: 0 },
    output: { sum: null, known_count: 0, unknown_count: 0 }
  },
  agent_usage: [],
  routing_distribution: { strategies: {}, selected_agents: [], rejection_reasons: {}, routing_failures: 0 },
  task_quality_success_rate: null
}
const agentId = '11111111-1111-4111-8111-111111111111'
const mixed = {
  ...empty,
  total_runs: 7,
  run_status_counts: { pending: 1, running: 1, success: 1, failed: 2, rejected: 1, cancelled: 1 },
  execution_success_rate: { numerator: 1, denominator: 2, value: 0.5 },
  execution_failure_rate: { numerator: 1, denominator: 2, value: 0.5 },
  rejected_rate: { numerator: 1, denominator: 7, value: 1 / 7 },
  average_routing_latency_ms: { sample_count: 6, value: 35 },
  average_execution_latency_ms: { sample_count: 4, value: 76750 },
  token_usage: {
    total: { sum: 12, known_count: 2, unknown_count: 3 },
    input: { sum: 10, known_count: 2, unknown_count: 3 },
    output: { sum: 0, known_count: 1, unknown_count: 4 }
  },
  agent_usage: [{ agent_id: agentId, version: 1, started_count: 5 }],
  routing_distribution: {
    strategies: { semantic: 6, semantic_llm: 1 },
    selected_agents: [{ agent_id: agentId, version: 1, count: 5 }],
    rejection_reasons: { NO_SUITABLE_AGENT: 1 }, routing_failures: 0
  }
}
let component

before(async () => {
  const source = readFileSync(new URL('../src/components/AgentHubMetrics.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const code = compileScript(descriptor, { id: 'agenthub-metrics-test', inlineTemplate: true }).content
    .replaceAll('from "vue"', `from "${import.meta.resolve('vue')}"`)
    .replaceAll("from 'vue'", `from '${import.meta.resolve('vue')}'`)
  component = (await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)).default
})

async function renderMetrics(props) {
  const app = createSSRApp({ render: () => h(component, props) })
  app.use(createI18n({ legacy: false, locale: 'en', messages }))
  return renderToString(app)
}

test('empty data shows zero counts but no fabricated zero rates or latency', async () => {
  const html = await renderMetrics({ metrics: empty })
  assert.match(html, /Total runs[^<]*<\/[^>]+>\s*0/)
  assert.match(html, /0 \/ 0/)
  assert.doesNotMatch(html, /0\.0%/)
  assert.match(html, /n=0/)
  assert.match(html, /Not measured/)
})

test('mixed terminal facts preserve business classes and API rate denominators', async () => {
  const html = await renderMetrics({ metrics: mixed })
  for (const label of ['Pending', 'Running', 'Succeeded', 'Failed', 'Rejected', 'Cancelled']) {
    assert.match(html, new RegExp(label))
  }
  assert.match(html, /50\.0%[^<]*1 \/ 2/)
  assert.match(html, /14\.3%[^<]*1 \/ 7/)
  assert.match(html, /35\.0 ms[^<]*n=6/)
  assert.match(html, /76750\.0 ms[^<]*n=4/)
  assert.match(html, /includes human input wait/)
})

test('unknown token counts stay distinct from measured zero and routing distribution remains visible', async () => {
  const html = await renderMetrics({ metrics: mixed })
  assert.match(html, /12[^<]*known 2[^<]*unknown 3/)
  assert.match(html, /0[^<]*known 1[^<]*unknown 4/)
  assert.match(html, /semantic_llm[^<]*1/)
  assert.match(html, /NO_SUITABLE_AGENT[^<]*1/)
  assert.match(html, new RegExp(`${agentId}[^<]*v1[^<]*5`))
  assert.match(html, /Strategies/)
  assert.match(html, /Selected agents/)
  assert.match(html, /Rejection reasons/)
  assert.match(html, /Not measured/)
})

test('all unknown usage remains unknown instead of becoming measured zero', async () => {
  const html = await renderMetrics({
    metrics: {
      ...mixed,
      token_usage: {
        total: { sum: null, known_count: 0, unknown_count: 5 },
        input: { sum: null, known_count: 0, unknown_count: 5 },
        output: { sum: null, known_count: 0, unknown_count: 5 }
      }
    }
  })
  assert.match(html, /Total: — \(known 0, unknown 5\)/)
  assert.doesNotMatch(html, /Total: 0 \(known 0, unknown 5\)/)
})

test('read errors show only a safe code and never pretend metrics are zero', async () => {
  const html = await renderMetrics({ metrics: null, error: 'METRICS_DATA_INVALID' })
  assert.match(html, /Metrics unavailable/)
  assert.match(html, /METRICS_DATA_INVALID/)
  assert.doesNotMatch(html, /Total runs|0\.0%/)
  const unsafe = await renderMetrics({ metrics: null, error: 'secret=raw-value' })
  assert.doesNotMatch(unsafe, /secret=raw-value/)
})
