import test from 'node:test'
import assert from 'node:assert/strict'
import { projectAgentHubRun, isTerminalAgentHubRun } from '../src/utils/agentHubRunState.js'

const record = (status, changes = {}) => ({
  run_id: 'run-1', session_id: 'session-1', status, ...changes
})

test('query snapshots advance pending to running to failed without native status inference', () => {
  const pending = projectAgentHubRun(null, record('pending'), 'run-1', 'session-1')
  const running = projectAgentHubRun(pending, record('running'), 'run-1', 'session-1')
  const failed = projectAgentHubRun(running, record('failed', { error_code: 'EXECUTION_FAILED' }), 'run-1', 'session-1')
  assert.equal(failed.status, 'failed')
  assert.equal(failed.error_code, 'EXECUTION_FAILED')
  assert.equal(isTerminalAgentHubRun(failed), true)
})

test('wrong run, wrong session and invalid status cannot replace a snapshot', () => {
  const current = record('running')
  assert.equal(projectAgentHubRun(current, record('success', { run_id: 'run-2' }), 'run-1', 'session-1'), current)
  assert.equal(projectAgentHubRun(current, record('success', { session_id: 'session-2' }), 'run-1', 'session-1'), current)
  assert.equal(projectAgentHubRun(current, record('completed'), 'run-1', 'session-1'), current)
  assert.equal(projectAgentHubRun(current, null, 'run-1', 'session-1'), current)
})

test('confirmed terminal state resists late running and conflicting terminal snapshots', () => {
  const cancelled = record('cancelled')
  assert.equal(projectAgentHubRun(cancelled, record('running'), 'run-1', 'session-1'), cancelled)
  assert.equal(projectAgentHubRun(cancelled, record('success'), 'run-1', 'session-1'), cancelled)
  assert.equal(isTerminalAgentHubRun(record('pending')), false)
  assert.equal(isTerminalAgentHubRun(record('running')), false)
})
