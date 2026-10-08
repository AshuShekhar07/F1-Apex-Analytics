/** "Andrea Kimi Antonelli" -> ["Andrea Kimi", "Antonelli"]: F1 graphics show the surname big. */
export function splitName(name: string): [string, string] {
  const parts = name.trim().split(/\s+/)
  return [parts.slice(0, -1).join(' '), parts[parts.length - 1]]
}

/** The three-letter timing-screen code: "Nico Hülkenberg" -> "HUL". */
export function driverCode(name: string): string {
  const surname = splitName(name)[1].normalize('NFD').replace(/[̀-ͯ]/g, '')
  return surname.replace(/[^A-Za-z]/g, '').slice(0, 3).toUpperCase()
}
