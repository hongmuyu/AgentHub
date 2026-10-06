<template>
  <details class="agenthub-metrics">
    <summary>{{ $t('launch.metrics_title') }}<span v-if="error"> · {{ $t('launch.metrics_unavailable') }}</span></summary>
    <button type="button" :disabled="loading" @click="emit('refresh')">{{ $t('launch.metrics_refresh') }}</button>
    <p v-if="loading && !metrics">{{ $t('launch.loading') }}</p>
    <p v-if="error">{{ $t('launch.metrics_unavailable') }}<template v-if="safeError"> · {{ safeError }}</template></p>
    <div v-if="metrics" class="metrics-content">
      <p><strong>{{ $t('launch.metrics_total_runs') }}</strong> {{ metrics.total_runs }}</p>

      <h4>{{ $t('launch.metrics_business_states') }}</h4>
      <ul>
        <li v-for="state in states" :key="state">
          {{ $t(`launch.metrics_${state}`) }}: {{ metrics.run_status_counts[state] }}
        </li>
      </ul>

      <h4>{{ $t('launch.metrics_rates') }}</h4>
      <ul>
        <li>{{ $t('launch.metrics_execution_success') }}: {{ formatRate(metrics.execution_success_rate) }} ({{ fraction(metrics.execution_success_rate) }})</li>
        <li>{{ $t('launch.metrics_execution_failure') }}: {{ formatRate(metrics.execution_failure_rate) }} ({{ fraction(metrics.execution_failure_rate) }})</li>
        <li>{{ $t('launch.metrics_rejected_rate') }}: {{ formatRate(metrics.rejected_rate) }} ({{ fraction(metrics.rejected_rate) }})</li>
      </ul>

      <h4>{{ $t('launch.metrics_latency') }}</h4>
      <ul>
        <li>{{ $t('launch.metrics_routing_latency') }}: {{ formatAverage(metrics.average_routing_latency_ms) }} (n={{ metrics.average_routing_latency_ms.sample_count }})</li>
        <li>{{ $t('launch.metrics_execution_latency') }}: {{ formatAverage(metrics.average_execution_latency_ms) }} (n={{ metrics.average_execution_latency_ms.sample_count }})</li>
      </ul>
      <p class="metrics-note">{{ $t('launch.metrics_execution_wait_note') }}</p>

      <h4>{{ $t('launch.metrics_tokens') }}</h4>
      <ul>
        <li v-for="kind in tokenKinds" :key="kind">
          {{ $t(`launch.metrics_tokens_${kind}`) }}: {{ formatToken(metrics.token_usage[kind]) }}
          ({{ $t('launch.metrics_known') }} {{ metrics.token_usage[kind].known_count }}, {{ $t('launch.metrics_unknown') }} {{ metrics.token_usage[kind].unknown_count }})
        </li>
      </ul>

      <h4>{{ $t('launch.metrics_agent_usage') }}</h4>
      <ul v-if="metrics.agent_usage.length">
        <li v-for="agent in metrics.agent_usage" :key="`${agent.agent_id}:${agent.version}`">
          {{ agent.agent_id }} v{{ agent.version }}: {{ agent.started_count }}
        </li>
      </ul>
      <p v-else>{{ $t('launch.metrics_none') }}</p>

      <h4>{{ $t('launch.metrics_routing_distribution') }}</h4>
      <h5>{{ $t('launch.metrics_strategies') }}</h5>
      <ul>
        <li v-for="(count, strategy) in metrics.routing_distribution.strategies" :key="`strategy:${strategy}`">{{ strategy }}: {{ count }}</li>
      </ul>
      <h5>{{ $t('launch.metrics_selected_agents') }}</h5>
      <ul>
        <li v-for="agent in metrics.routing_distribution.selected_agents" :key="`selected:${agent.agent_id}:${agent.version}`">
          {{ agent.agent_id }} v{{ agent.version }}: {{ agent.count }}
        </li>
      </ul>
      <h5>{{ $t('launch.metrics_rejection_reasons') }}</h5>
      <ul>
        <li v-for="(count, reason) in metrics.routing_distribution.rejection_reasons" :key="`reason:${reason}`">{{ reason }}: {{ count }}</li>
      </ul>
      <p>{{ $t('launch.metrics_routing_failures') }}: {{ metrics.routing_distribution.routing_failures }}</p>

      <p>{{ $t('launch.metrics_quality') }}: <template v-if="metrics.task_quality_success_rate">{{ formatRate(metrics.task_quality_success_rate) }} ({{ fraction(metrics.task_quality_success_rate) }})</template><template v-else>{{ $t('launch.metrics_not_measured') }}</template></p>
    </div>
  </details>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  metrics: { type: Object, default: null },
  error: { type: String, default: null },
  loading: { type: Boolean, default: false }
})
const emit = defineEmits(['refresh'])
const states = ['pending', 'running', 'success', 'failed', 'rejected', 'cancelled']
const tokenKinds = ['total', 'input', 'output']
const safeError = computed(() => typeof props.error === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(props.error) ? props.error : null)
const formatRate = (rate) => rate?.denominator > 0 && Number.isFinite(rate.value)
  ? `${(rate.value * 100).toFixed(1)}%` : '—'
const fraction = (rate) => `${rate.numerator} / ${rate.denominator}`
const formatAverage = (average) => average?.sample_count > 0 && Number.isFinite(average.value)
  ? `${average.value.toFixed(1)} ms` : '—'
const formatToken = (count) => count.known_count > 0 ? count.sum : '—'
</script>

<style scoped>
.agenthub-metrics { margin-top: 1rem; padding: 1rem; border: 1px solid #414651; border-radius: 8px; color: #e4e7ec; }
.agenthub-metrics summary { cursor: pointer; font-weight: 600; }
.agenthub-metrics button { margin-top: 0.75rem; padding: 0.4rem 0.7rem; border: 1px solid #555d69; border-radius: 6px; background: #323944; color: #e4e7ec; cursor: pointer; }
.agenthub-metrics button:disabled { opacity: 0.6; cursor: default; }
.metrics-content { margin-top: 0.75rem; font-size: 0.85rem; overflow-wrap: anywhere; }
.metrics-content h4 { margin: 0.8rem 0 0.3rem; }
.metrics-content h5 { margin: 0.5rem 0 0.2rem; }
.metrics-content p { margin: 0.4rem 0; }
.metrics-content ul { margin: 0.3rem 0; padding-left: 1.25rem; }
.metrics-content li { margin: 0.2rem 0; }
.metrics-note { color: #adb5c3; }
</style>
