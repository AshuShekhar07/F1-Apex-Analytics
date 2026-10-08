/** "Andrea Kimi Antonelli" -> ["Andrea Kimi", "Antonelli"]: F1 graphics show the surname big. */
export function splitName(name: string): [string, string] {
  const parts = name.trim().split(/\s+/)
  return [parts.slice(0, -1).join(' '), parts[parts.length - 1]]
}
