const BUSINESS_STATES = ['pending', 'running', 'success', 'failed', 'rejected', 'cancelled']
const TERMINAL_STATES = ['success', 'failed', 'rejected', 'cancelled']

export const isTerminalAgentHubRun = (run) => TERMINAL_STATES.includes(run?.status)

export function projectAgentHubRun(current, incoming, runId, sessionId) {
  if (
    !incoming || incoming.run_id !== runId ||
    (sessionId && incoming.session_id !== sessionId) ||
    !BUSINESS_STATES.includes(incoming.status) ||
    isTerminalAgentHubRun(current)
  ) {
    return current
  }
  return incoming
}
