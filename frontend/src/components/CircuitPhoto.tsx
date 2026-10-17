import { useState } from 'react'

import { useAsync } from '../hooks'
import { bestMatch } from '../trackMatch'
import type { CircuitPhoto } from '../types'

/** Credits file written by fetch_circuit_photos_v1.py; missing (no photos yet) is fine. */
let photosOnce: Promise<Record<string, CircuitPhoto>> | null = null
function loadPhotos(): Promise<Record<string, CircuitPhoto>> {
  photosOnce ??= fetch('/circuit-photos/photos.json')
    .then((r) => (r.ok ? r.json() : { photos: {} }))
    .then((d: { photos?: Record<string, CircuitPhoto> }) => d.photos ?? {})
    .catch(() => ({}))
  return photosOnce
}

/** The photo of a circuit, by its circuit id or by a database track name; null while loading or
    when there is none. */
export function useCircuitPhoto(by: { id?: string | null; track?: string | null }): CircuitPhoto | null {
  const photos = useAsync(loadPhotos, []).data
  if (!photos) return null
  if (by.id) return photos[by.id] ?? null
  return bestMatch(Object.values(photos), by.track)
}

/**
 * A circuit photo filling its parent (which must be position: relative), darkened so the text on
 * top stays readable, with the author and licence credited in a corner. Fades in once loaded;
 * renders nothing if the image fails.
 */
export function CircuitPhotoBg({ photo }: { photo: CircuitPhoto | null }) {
  const [state, setState] = useState<{ src: string; ok: boolean } | null>(null)
  if (!photo || (state?.src === photo.src && !state.ok)) return null
  const loaded = state?.src === photo.src && state.ok
  return (
    <>
      <div className={`photo-bg ${loaded ? 'in' : ''}`} aria-hidden="true">
        <img src={photo.src} alt="" decoding="async" onLoad={() => setState({ src: photo.src, ok: true })}
             onError={() => setState({ src: photo.src, ok: false })} />
      </div>
      {loaded && (
        <p className="photo-credit">
          Photo: {photo.author} · {photo.license_url ? <a href={photo.license_url} target="_blank" rel="noreferrer">{photo.license}</a> : photo.license}
          {photo.page && <> · <a href={photo.page} target="_blank" rel="noreferrer">Wikimedia Commons</a></>}
        </p>
      )}
    </>
  )
}
