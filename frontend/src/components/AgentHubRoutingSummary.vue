<template>
  <section class="routing-summary" :aria-label="$t('launch.routing_summary')">
    <h3>{{ $t('launch.routing_summary') }}</h3>

    <p v-if="summary?.routing_status === 'selected'" class="routing-decision">
      <template v-if="selectedAgent">
        {{ $t('launch.selected_agent') }}:
        <strong>{{ agentName(selectedAgent) }}</strong>
        <span>{{ selectedAgent.id }} · v{{ selectedAgent.version }}</span>
      </template>
      <template v-else>{{ $t('launch.selected_unavailable') }}</template>
    </p>
    <p v-else-if="summary?.routing_status === 'rejected'" class="routing-decision">
      {{ $t('launch.no_suitable_agent') }} · {{ safeCode(summary.error_code) }}
    </p>
    <p v-else-if="summary?.routing_status === 'failed'" class="routing-decision">
      {{ $t('launch.routing_failed') }} · {{ safeCode(summary.error_code) }}
    </p>

    <h4>{{ $t('launch.recall_candidates') }}</h4>
    <ol v-if="candidates.length" class="routing-candidates">
      <li v-for="(candidate, index) in candidates" :key="`${candidate?.id || index}-${candidate?.version || index}`">
        <strong>{{ agentName(candidate) }}</strong>
        <span v-if="candidate?.id">{{ candidate.id }}<template v-if="validVersion(candidate.version)"> · v{{ candidate.version }}</template></span>
        <span v-if="candidate?.score_kind === 'cosine' && validScore(candidate.raw_similarity)">
          {{ $t('launch.raw_cosine') }}: {{ candidate.raw_similarity }}
        </span>
        <span v-else>{{ $t('launch.score_unavailable') }}</span>
      </li>
    </ol>
    <p v-else>{{ $t('launch.no_candidates') }}</p>

    <template v-if="Array.isArray(summary?.rerank_order)">
      <h4>{{ $t('launch.rerank_order') }}</h4>
      <ol v-if="rerankedCandidates.length" class="routing-rerank">
        <li v-for="candidate in rerankedCandidates" :key="`${candidate.id}-${candidate.version}`">
          {{ agentName(candidate) }} · {{ candidate.id }} · v{{ candidate.version }}
        </li>
      </ol>
      <p v-else>{{ $t('launch.rerank_unavailable') }}</p>
    </template>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ summary: { type: Object, required: true } })
const candidates = computed(() => Array.isArray(props.summary?.candidates) ? props.summary.candidates : [])
const validVersion = (version) => Number.isInteger(version) && version > 0
const validScore = (score) => typeof score === 'number' && Number.isFinite(score) && score >= -1 && score <= 1
const agentName = (agent) => typeof agent?.name === 'string' && agent.name.trim()
  ? agent.name.trim() : agent?.id ? `Agent ${agent.id}` : 'Agent'
const safeCode = (code) => typeof code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(code)
  ? code : 'UNKNOWN'
const selectedAgent = computed(() => {
  const agent = props.summary?.selected_agent
  return typeof agent?.id === 'string' && agent.id && validVersion(agent.version) ? agent : null
})
const rerankedCandidates = computed(() => {
  if (!Array.isArray(props.summary?.rerank_order)) return []
  const ordered = props.summary.rerank_order.map((ref) => candidates.value.find(
    (candidate) => candidate?.id === ref?.agent_id && candidate?.version === ref?.version
  ))
  return ordered.every(Boolean) ? ordered : []
})
</script>

<style scoped>
.routing-summary { margin-top: 1rem; padding: 1rem; border: 1px solid #414651; border-radius: 8px; color: #e4e7ec; }
.routing-summary h3, .routing-summary h4 { margin: 0 0 0.5rem; }
.routing-summary h4 { margin-top: 0.9rem; font-size: 0.9rem; }
.routing-summary p { margin: 0.35rem 0; }
.routing-summary ol { margin: 0; padding-left: 1.5rem; }
.routing-summary li { padding: 0.25rem 0; overflow-wrap: anywhere; }
.routing-candidates li span, .routing-decision span { display: block; color: #adb5c3; font-size: 0.85rem; }
</style>
