import { teamColor } from '../format'

export function DriverName({ name, color, team }: { name: string; color?: string | null; team?: string | null }) {
  return (
    <span className="driver">
      <i className="team-stripe" style={{ background: teamColor(color) }} />
      <span>
        {name}
        {team && <span className="muted small team-name">{team}</span>}
      </span>
    </span>
  )
}
