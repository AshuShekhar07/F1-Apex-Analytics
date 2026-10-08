import { useEffect, useState } from 'react'

import { musicState, onMusicChange, setMusicEnabled, type MusicState } from './music'
import { onSoundChange, setSoundEnabled, soundEnabled } from './sound'

/** Top-bar audio switches: background music (bars dance while it plays) and UI sound effects. */
export function SoundToggle() {
  const [music, setMusic] = useState<MusicState>(musicState)
  const [fx, setFx] = useState(soundEnabled)
  useEffect(() => onMusicChange(setMusic), [])
  useEffect(() => onSoundChange(setFx), [])
  const musicOn = music !== 'off'
  return (
    <div className="audio-controls">
      <button
        type="button"
        className={`sound-toggle ${music}`}
        onClick={() => setMusicEnabled(!musicOn)}
        aria-pressed={musicOn}
        aria-label={musicOn ? 'Background music on: click to mute' : 'Background music muted: click to play'}
        title={music === 'waiting' ? 'Music starts after your first click' : undefined}
        data-sound-click="toggle"
        data-cursor={musicOn ? 'Mute' : 'Play'}
      >
        <span className="eq" aria-hidden="true"><i /><i /><i /><i /></span>
        <span className="txt">{musicOn ? 'Music' : 'Muted'}</span>
      </button>
      <button
        type="button"
        className={`sound-toggle fx ${fx ? 'playing' : 'off'}`}
        onClick={() => setSoundEnabled(!fx)}
        aria-pressed={fx}
        aria-label={fx ? 'Sound effects on: click to mute' : 'Sound effects off: click to turn on'}
        data-sound-click="toggle"
        data-cursor={fx ? 'Mute FX' : 'FX'}
      >
        <span className="txt">FX</span>
      </button>
    </div>
  )
}
