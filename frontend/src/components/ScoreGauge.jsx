export default function ScoreGauge({ score }) {
  const color = score >= 70 ? '#4CAF50' : score >= 50 ? '#FFC107' : '#FF5370'
  const label = score >= 70 ? 'High' : score >= 50 ? 'Medium' : 'Low'
  const radius = 36
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (score / 100) * circumference

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width="90" height="90" viewBox="0 0 90 90">
        <circle cx="45" cy="45" r={radius} fill="none" stroke="#2A2D3E" strokeWidth="8" />
        <circle
          cx="45" cy="45" r={radius}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 45 45)"
          style={{ transition: 'stroke-dashoffset 0.6s ease' }}
        />
        <text x="45" y="49" textAnchor="middle" fill={color} fontSize="16" fontWeight="700">
          {score}%
        </text>
      </svg>
      <span className="text-xs font-medium" style={{ color }}>{label}</span>
    </div>
  )
}
