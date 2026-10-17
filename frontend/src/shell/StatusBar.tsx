import { useEffect, useState } from 'react'

import { onReconnecting } from '../net'
import { onUpdateReady, updateReady } from '../updates'

/** A thin bar under the header: "reconnecting" while the server restarts (requests are being
    retried and the page fills in by itself), and "new version" after a deployment. */
export function StatusBar() {
  const [waiting, setWaiting] = useState(0)
  const [update, setUpdate] = useState(updateReady)
  useEffect(() => onReconnecting(setWaiting), [])
  useEffect(() => onUpdateReady(() => setUpdate(true)), [])

  if (waiting > 0) {
    return (
      <div className="status-bar" role="status">
        <i className="spin" aria-hidden="true" />
        Reconnecting to the Apex21 server, which may be restarting after an update. This page fills in by itself as soon as it answers.
      </div>
    )
  }
  if (update) {
    return (
      <div className="status-bar update" role="status">
        A new version of Apex21 is out: it loads when you open another page.
        <button type="button" className="btn" onClick={() => window.location.reload()}>Load it now</button>
      </div>
    )
  }
  return null
}
