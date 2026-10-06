<template>
  <section class="business-run" :aria-label="$t('launch.business_run_state')">
    <h3>{{ $t('launch.business_run_state') }}</h3>
    <p v-if="run">
      <strong v-if="businessStatus">{{ $t(`launch.business_${businessStatus}`) }}</strong>
      <strong v-else>{{ $t('launch.business_unknown') }}</strong>
    </p>
    <p v-else>{{ $t('launch.business_unavailable') }}</p>

    <p v-if="run && cancelRequested && !terminal" class="business-note">
      {{ $t('launch.business_cancel_requested') }}
    </p>
    <p v-if="run && recovered" class="business-note">
      {{ $t('launch.business_recovered_note') }}
    </p>
    <p v-if="businessStatus === 'success'" class="business-note">
      {{ $t('launch.business_quality_note') }}
    </p>
    <p v-if="safeCode(run?.error_code)" class="business-note">
      {{ $t('launch.business_reason') }}: {{ safeCode(run.error_code) }}
    </p>
    <p v-if="safeResultRef" class="business-note">
      {{ $t('launch.business_result_ref') }}: {{ safeResultRef }}
    </p>
    <p v-if="queryError" class="business-note">
      {{ $t('launch.business_query_failed') }}<template v-if="safeCode(queryError)"> · {{ safeCode(queryError) }}</template>
    </p>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  run: { type: Object, default: null },
  queryError: { type: String, default: null },
  cancelRequested: { type: Boolean, default: false },
  recovered: { type: Boolean, default: false }
})
const states = ['pending', 'running', 'success', 'failed', 'rejected', 'cancelled']
const businessStatus = computed(() => states.includes(props.run?.status) ? props.run.status : null)
const terminal = computed(() => ['success', 'failed', 'rejected', 'cancelled'].includes(businessStatus.value))
const safeCode = (code) => typeof code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(code) ? code : null
const safeResultRef = computed(() => {
  const value = props.run?.result_ref
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$/.test(value)
    && !/(?:api[_-]?key|token|secret|password)/i.test(value) ? value : null
})
</script>

<style scoped>
.business-run { margin-top: 1rem; padding: 1rem; border: 1px solid #414651; border-radius: 8px; color: #e4e7ec; }
.business-run h3 { margin: 0 0 0.5rem; }
.business-run p { margin: 0.4rem 0; overflow-wrap: anywhere; }
.business-note { color: #adb5c3; font-size: 0.85rem; }
</style>
