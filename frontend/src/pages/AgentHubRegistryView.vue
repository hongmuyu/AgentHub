<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  fetchAgentHubAgents, registerAgentHubAgent, updateAgentHubAgent,
  setAgentHubAgentEnabled
} from '../utils/apiFunctions.js'

const { t } = useI18n()
const agents = ref([])
const loading = ref(false)
const busy = ref(false)
const listError = ref('')
const actionError = ref('')
const actionSuccess = ref('')
const editingId = ref(null)
const form = reactive({
  name: '', description: '', capabilities: '', tools: '', tags: '', runtime_ref: ''
})

const safeCode = (value, fallback) =>
  typeof value === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(value) ? value : fallback

async function responseError(response, fallback) {
  const body = await response.json().catch(() => ({}))
  return safeCode(body?.detail?.code, fallback)
}

async function loadAgents() {
  loading.value = true
  listError.value = ''
  try {
    const all = []
    for (let offset = 0; ; offset += 100) {
      const response = await fetchAgentHubAgents(offset)
      if (!response.ok) {
        listError.value = await responseError(response, 'REGISTRY_READ_FAILED')
        agents.value = []
        return false
      }
      const body = await response.json()
      if (!Array.isArray(body?.agents)) {
        listError.value = 'AGENT_LIST_INVALID'
        agents.value = []
        return false
      }
      all.push(...body.agents)
      if (body.agents.length < 100) break
    }
    agents.value = all
    return true
  } catch {
    listError.value = 'REGISTRY_READ_FAILED'
    agents.value = []
    return false
  } finally {
    loading.value = false
  }
}

function resetForm() {
  editingId.value = null
  Object.assign(form, {
    name: '', description: '', capabilities: '', tools: '', tags: '', runtime_ref: ''
  })
}

function editAgent(agent) {
  actionError.value = ''
  actionSuccess.value = ''
  editingId.value = agent.id
  Object.assign(form, {
    name: agent.name,
    description: agent.description,
    capabilities: agent.capabilities.join('\n'),
    tools: agent.tools.join('\n'),
    tags: agent.tags.join('\n'),
    runtime_ref: agent.runtime_ref
  })
}

const lines = (value) => value.split('\n').map((item) => item.trim()).filter(Boolean)

async function saveAgent() {
  if (busy.value || loading.value) return
  actionError.value = ''
  actionSuccess.value = ''
  const metadata = {
    name: form.name.trim(),
    description: form.description.trim(),
    capabilities: lines(form.capabilities),
    tools: lines(form.tools),
    tags: lines(form.tags),
    runtime_ref: form.runtime_ref.trim()
  }
  if (!metadata.name || !metadata.description || !metadata.capabilities.length || !metadata.runtime_ref) {
    actionError.value = 'INVALID_AGENT_METADATA'
    return
  }
  busy.value = true
  try {
    const response = editingId.value
      ? await updateAgentHubAgent(editingId.value, metadata)
      : await registerAgentHubAgent(metadata)
    if (!response.ok) {
      actionError.value = await responseError(response, 'REGISTRY_WRITE_FAILED')
      return
    }
    if (await loadAgents()) {
      actionSuccess.value = t(`agenthub_registry.${editingId.value ? 'updated' : 'registered'}`)
      resetForm()
    }
  } catch {
    actionError.value = 'REGISTRY_WRITE_FAILED'
  } finally {
    busy.value = false
  }
}

async function toggleAgent(agent) {
  if (busy.value || loading.value) return
  actionError.value = ''
  actionSuccess.value = ''
  const enable = agent.status === 'disabled'
  busy.value = true
  try {
    const response = await setAgentHubAgentEnabled(agent.id, enable)
    if (!response.ok) {
      actionError.value = await responseError(response, 'REGISTRY_WRITE_FAILED')
      return
    }
    if (await loadAgents()) {
      actionSuccess.value = t(`agenthub_registry.${enable ? 'enabled' : 'disabled_action'}`)
    }
  } catch {
    actionError.value = 'REGISTRY_WRITE_FAILED'
  } finally {
    busy.value = false
  }
}

onMounted(loadAgents)
</script>

<template>
  <div class="registry-page">
    <div class="registry-shell">
      <header class="page-header">
        <div>
          <p class="eyebrow">AgentHub</p>
          <h1>{{ t('agenthub_registry.title') }}</h1>
          <p class="subtitle">{{ t('agenthub_registry.subtitle') }}</p>
        </div>
        <button type="button" class="secondary-button" :disabled="loading || busy" @click="loadAgents">
          {{ t('agenthub_registry.refresh') }}
        </button>
      </header>

      <div class="registry-layout">
        <section class="catalog-panel" aria-label="Agent catalog">
          <div class="section-heading">
            <h2>{{ t('agenthub_registry.title') }}</h2>
            <span v-if="!loading && !listError" class="count">{{ agents.length }}</span>
          </div>
          <p v-if="loading" role="status">{{ t('agenthub_registry.loading') }}</p>
          <p v-else-if="listError" role="alert" class="error">
            {{ t('agenthub_registry.list_error') }} <code>{{ listError }}</code>
          </p>
          <p v-else-if="agents.length === 0" class="empty">{{ t('agenthub_registry.empty') }}</p>
          <div v-else class="agent-list">
            <article v-for="agent in agents" :key="agent.id" class="agent-card">
              <div class="agent-topline">
                <h3>{{ agent.name }}</h3>
                <span class="status" :class="agent.status">
                  {{ t(`agenthub_registry.${agent.status}`) }}
                </span>
              </div>
              <p class="agent-description">{{ agent.description }}</p>
              <dl>
                <dt>{{ t('agenthub_registry.capabilities') }}</dt>
                <dd>{{ agent.capabilities.join(' · ') }}</dd>
                <dt>{{ t('agenthub_registry.version') }}</dt>
                <dd>v{{ agent.version }}</dd>
                <dt>{{ t('agenthub_registry.runtime_ref') }}</dt>
                <dd><code>{{ agent.runtime_ref }}</code></dd>
              </dl>
              <div class="card-actions">
                <button type="button" class="text-button" :disabled="loading || busy" @click="editAgent(agent)">
                  {{ t('agenthub_registry.edit') }}
                </button>
                <button type="button" class="secondary-button" :disabled="loading || busy" @click="toggleAgent(agent)">
                  {{ t(`agenthub_registry.${agent.status === 'active' ? 'disable' : 'enable'}`) }}
                </button>
              </div>
            </article>
          </div>
        </section>

        <section class="form-panel" aria-label="Agent metadata">
          <h2>{{ t(`agenthub_registry.${editingId ? 'update' : 'register'}`) }}</h2>
          <p v-if="actionError" role="alert" class="error">
            {{ t(actionError === 'INVALID_AGENT_METADATA' ? 'agenthub_registry.invalid_metadata' : 'agenthub_registry.action_error') }}
            <code>{{ actionError }}</code>
          </p>
          <p v-if="actionSuccess" role="status" class="success">{{ actionSuccess }}</p>
          <form @submit.prevent="saveAgent">
            <label>{{ t('agenthub_registry.name') }}
              <input v-model="form.name" required maxlength="80" />
            </label>
            <label>{{ t('agenthub_registry.description') }}
              <textarea v-model="form.description" required maxlength="1000" rows="3" />
            </label>
            <label>{{ t('agenthub_registry.capabilities') }}
              <textarea v-model="form.capabilities" required rows="3" :placeholder="t('agenthub_registry.items_hint')" />
            </label>
            <label>{{ t('agenthub_registry.tools') }}
              <textarea v-model="form.tools" rows="2" :placeholder="t('agenthub_registry.items_hint')" />
            </label>
            <label>{{ t('agenthub_registry.tags') }}
              <textarea v-model="form.tags" rows="2" :placeholder="t('agenthub_registry.items_hint')" />
            </label>
            <label>{{ t('agenthub_registry.runtime_ref') }}
              <input v-model="form.runtime_ref" required maxlength="128" placeholder="workflow://research-agent/1" />
            </label>
            <p class="field-hint">{{ t('agenthub_registry.runtime_ref_hint') }}</p>
            <div class="form-actions">
              <button type="submit" class="primary-button" :disabled="loading || busy">
                {{ t(`agenthub_registry.${editingId ? 'update' : 'register'}`) }}
              </button>
              <button v-if="editingId" type="button" class="text-button" :disabled="busy" @click="resetForm">
                {{ t('agenthub_registry.cancel_edit') }}
              </button>
            </div>
          </form>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.registry-page { min-height: calc(100vh - 55px); background: #f6f8f8; color: #21302e; font-family: 'Inter', sans-serif; }
.registry-shell { max-width: 1320px; margin: 0 auto; padding: 40px 32px 64px; }
.page-header, .section-heading, .agent-topline, .card-actions, .form-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.page-header { margin-bottom: 32px; }
.eyebrow { margin: 0 0 8px; color: #2b7563; font-size: 12px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
h1 { margin: 0; font-size: 32px; letter-spacing: -.03em; }
h2 { margin: 0 0 20px; font-size: 19px; }
h3 { margin: 0; font-size: 17px; }
.subtitle { color: #5d6c68; margin: 8px 0 0; }
.registry-layout { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(300px, 1fr); gap: 24px; align-items: start; }
.catalog-panel, .form-panel { background: white; border: 1px solid #dce5e1; border-radius: 12px; padding: 24px; box-shadow: 0 10px 28px rgba(23, 44, 37, .04); }
.section-heading { margin-bottom: 18px; }
.section-heading h2 { margin: 0; }
.count { color: #5d6c68; font-size: 14px; }
.agent-list { display: grid; gap: 14px; }
.agent-card { border: 1px solid #dce5e1; border-radius: 9px; padding: 18px; }
.agent-description { color: #51615c; line-height: 1.5; margin: 10px 0 18px; overflow-wrap: anywhere; }
.status { padding: 4px 9px; border-radius: 20px; font-size: 12px; font-weight: 700; }
.status.active { color: #16644d; background: #e0f4e9; }
.status.disabled { color: #76552d; background: #f7ecdc; }
dl { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 8px 12px; margin: 0; font-size: 13px; }
dt { color: #65746f; }
dd { margin: 0; overflow-wrap: anywhere; }
code { font-size: 12px; overflow-wrap: anywhere; }
.card-actions { justify-content: flex-end; margin-top: 18px; }
form { display: grid; gap: 16px; }
label { display: grid; gap: 7px; font-size: 13px; font-weight: 600; }
input, textarea { box-sizing: border-box; width: 100%; border: 1px solid #cbd8d2; border-radius: 7px; padding: 10px 12px; font: inherit; font-size: 14px; color: #21302e; background: white; }
input:focus, textarea:focus { outline: 2px solid #8bd9bb; border-color: #388a6f; }
textarea { resize: vertical; }
.field-hint { color: #687872; font-size: 12px; line-height: 1.5; margin: -8px 0 0; }
button { cursor: pointer; font: inherit; font-size: 13px; font-weight: 600; }
button:disabled { opacity: .55; cursor: not-allowed; }
.primary-button, .secondary-button { border-radius: 7px; padding: 9px 14px; }
.primary-button { color: white; background: #276e59; border: 1px solid #276e59; }
.secondary-button { color: #276e59; background: white; border: 1px solid #a9c8bb; }
.text-button { color: #276e59; border: 0; background: none; padding: 8px; }
.form-actions { justify-content: flex-start; }
.empty { padding: 36px 12px; text-align: center; color: #64736e; border: 1px dashed #cbd8d2; border-radius: 8px; }
.error, .success { border-radius: 7px; padding: 11px 13px; font-size: 13px; line-height: 1.5; }
.error { color: #902e34; background: #fff0f0; }
.success { color: #1c654f; background: #e5f6eb; }
.error code { margin-left: 5px; }
@media (max-width: 900px) { .registry-layout { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .registry-shell { padding: 24px 16px 48px; } .page-header { align-items: flex-start; } h1 { font-size: 26px; } dl { grid-template-columns: 1fr; gap: 4px; } dt:not(:first-child) { margin-top: 8px; } }
</style>
