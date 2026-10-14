/**
 * A side view of a generic Formula 1 car painted in a livery's colours: shown when no freely
 * licensed photo of the car is available. Our own drawing, not the real car's shape.
 */
export function LiveryDrawing({ colours, label }: { colours: [string, string, string]; label: string }) {
  const [body, second, accent] = colours
  return (
    <svg className="livery-drawing" viewBox="0 0 400 130" role="img" aria-label={`${label}: drawing in the livery's colours`}>
      <ellipse cx="200" cy="114" rx="175" ry="6" fill="#000" opacity="0.35" />
      {/* rear wing */}
      <rect x="24" y="30" width="34" height="9" rx="1" fill={second} />
      <rect x="24" y="41" width="30" height="5" rx="1" fill={body} />
      <rect x="22" y="28" width="7" height="52" rx="1" fill={body} />
      {/* floor and body */}
      <path d="M40 92 L372 92 L380 98 L36 98 Z" fill="#141414" />
      <path d="M44 90 C44 74 60 66 96 64 L150 58 C156 46 164 40 172 40 L178 40 C184 46 186 52 190 56 L228 58
               C262 62 300 70 346 78 C362 81 374 84 380 88 L380 92 L44 92 Z" fill={body} />
      {/* engine cover and sidepod in the second colour */}
      <path d="M96 66 L150 60 C158 50 164 44 172 43 L174 58 C150 62 126 66 100 72 Z" fill={second} />
      <path d="M104 88 C110 76 150 70 200 70 L236 72 C240 80 238 86 232 90 L110 90 Z" fill={second} opacity="0.92" />
      {/* accent stripe along the car */}
      <path d="M70 78 L232 74 L360 84 L360 87 L232 78 L70 82 Z" fill={accent} />
      {/* cockpit, halo and helmet */}
      <circle cx="198" cy="55" r="7" fill="#e8e8ea" />
      <rect x="195" y="52" width="9" height="3" rx="1" fill="#111" />
      <path d="M182 60 C186 46 206 44 222 56" fill="none" stroke="#111" strokeWidth="3.5" strokeLinecap="round" />
      {/* front wing */}
      <path d="M332 96 L390 96 L392 101 L330 101 Z" fill={second} />
      <rect x="384" y="86" width="5" height="15" rx="1" fill={body} />
      {/* wheels */}
      {[86, 316].map((cx) => (
        <g key={cx}>
          <circle cx={cx} cy="92" r="21" fill="#0d0d0d" />
          <circle cx={cx} cy="92" r="12" fill="#2a2a2e" />
          <circle cx={cx} cy="92" r="16" fill="none" stroke={accent} strokeWidth="1.5" opacity="0.8" />
        </g>
      ))}
    </svg>
  )
}
