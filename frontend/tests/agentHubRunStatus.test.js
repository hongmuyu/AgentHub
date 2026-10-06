import test, { before } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createSSRApp, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'

const messages = { en: JSON.parse(readFileSync(new URL('../src/locales/en.json', import.meta.url))) }
let component

before(async () => {
  const source = readFileSync(new URL('../src/components/AgentHubRunStatus.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const code = compileScript(descriptor, { id: 'agenthub-run-status-test', inlineTemplate: true }).content
    .replaceAll('from "vue"', `from "${import.meta.resolve('vue')}"`)
    .replaceAll("from 'vue'", `from '${import.meta.resolve('vue')}'`)
  component = (await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)).default
})

async function renderStatus(props) {
  const app = createSSRApp({ render: () => h(component, props) })
  app.use(createI18n({ legacy: false, locale: 'en', messages }))
  return renderToString(app)
}

test('all six business states have separate labels', async () => {
  const states = {
    pending: 'Pending', running: 'Running', success: 'Execution succeeded',
    failed: 'Failed', rejected: 'Rejected', cancelled: 'Cancelled'
  }
  for (const [status, label] of Object.entries(states)) {
    const html = await renderStatus({ run: { status, run_id: 'run-1' } })
    assert.match(html, new RegExp(label))
    assert.match(html, /Business run state/)
  }
})

test('cancel request remains pending until the queried run is cancelled', async () => {
  const requested = await renderStatus({
    run: { status: 'running', run_id: 'run-1' }, cancelRequested: true
  })
  assert.match(requested, /Cancellation requested; awaiting business confirmation/)
  assert.doesNotMatch(requested, />Cancelled</)

  const confirmed = await renderStatus({
    run: { status: 'cancelled', run_id: 'run-1' }, cancelRequested: true
  })
  assert.match(confirmed, />Cancelled</)
  assert.doesNotMatch(confirmed, /awaiting business confirmation/)
})

test('recovered running state is explicitly a stored snapshot', async () => {
  const html = await renderStatus({ run: { status: 'running', run_id: 'run-1' }, recovered: true })
  assert.match(html, /Loaded from stored run record/)
  assert.match(html, /does not restart execution/)
  assert.doesNotMatch(html, /Execution succeeded/)
})

test('failure and safe result reference use public query fields only', async () => {
  const failed = await renderStatus({
    run: { status: 'failed', run_id: 'run-1', error_code: 'EXECUTION_FAILED' }
  })
  assert.match(failed, /EXECUTION_FAILED/)
  const success = await renderStatus({
    run: { status: 'success', run_id: 'run-1', result_ref: 'artifact:result-1' }
  })
  assert.match(success, /artifact:result-1/)
  assert.match(success, /Task quality is not assessed/)
  assert.doesNotMatch(success, /href=/)
})

test('query failure and incomplete data render without invented status or unsafe fields', async () => {
  const unavailable = await renderStatus({ run: null, queryError: 'RUN_NOT_FOUND' })
  assert.match(unavailable, /Business state unavailable/)
  assert.match(unavailable, /RUN_NOT_FOUND/)
  const partial = await renderStatus({
    run: { run_id: 'run-1', result_ref: '/tmp/private', error_code: 'unsafe detail' },
    queryError: 'RUN_QUERY_FAILED'
  })
  assert.match(partial, /Unknown business state/)
  assert.match(partial, /RUN_QUERY_FAILED/)
  assert.doesNotMatch(partial, /undefined|NaN|\/tmp\/private|unsafe detail/)
})
