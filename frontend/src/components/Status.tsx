export function Loading({ what }: { what: string }) {
  return <p className="muted">Loading {what}…</p>
}

export function ErrorBox({ error }: { error: Error }) {
  return (
    <div className="error-box">
      <strong>Could not load data.</strong> {error.message}
      <div className="muted small">Is the API running? (uvicorn app.main:app on port 8000)</div>
    </div>
  )
}

export function DataNotes({ notes }: { notes: string[] }) {
  if (!notes.length) return null
  return (
    <details className="notes">
      <summary>{notes.length} data note{notes.length > 1 ? 's' : ''}: where evidence was thin and what was used instead</summary>
      <ul>
        {notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </details>
  )
}
