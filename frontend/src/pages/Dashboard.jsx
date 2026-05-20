import { useEffect, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { getStats, triggerScrape, triggerScoring } from '../api'

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [scraping, setScraping] = useState(false)
  const [scoring, setScoring] = useState(false)

  useEffect(() => {
    getStats()
      .then(r => setStats(r.data))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  async function handleScrape() {
    setScraping(true)
    try {
      await triggerScrape({ max_jobs: 50 })
      const r = await getStats()
      setStats(r.data)
    } catch (e) {
      console.error(e)
    } finally {
      setScraping(false)
    }
  }

  async function handleScore() {
    setScoring(true)
    try {
      await triggerScoring()
      const r = await getStats()
      setStats(r.data)
    } catch (e) {
      console.error(e)
    } finally {
      setScoring(false)
    }
  }

  const chartData = stats ? [
    { name: 'High Match', value: stats.high_match, fill: '#4CAF50' },
    { name: 'Medium Match', value: stats.medium_match, fill: '#FFC107' },
    { name: 'Low Match', value: stats.low_match, fill: '#FF5370' },
  ] : []

  const statCards = [
    { label: 'Total Jobs', value: stats?.total_jobs ?? '-', color: 'text-accent' },
    { label: 'High Match (70%+)', value: stats?.high_match ?? '-', color: 'text-success' },
    { label: 'Applied', value: stats?.applied ?? '-', color: 'text-warning' },
    { label: 'Interviews', value: stats?.interviews ?? '-', color: 'text-textPrimary' },
  ]

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-textPrimary">Dashboard</h1>
          {stats?.last_scraped && (
            <p className="text-textSecondary text-sm mt-1">
              Last scraped: {new Date(stats.last_scraped).toLocaleString()}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button onClick={handleScrape} disabled={scraping}
            className="px-4 py-2 bg-accent text-bg text-sm font-medium rounded hover:bg-accent/90 disabled:opacity-50 transition-colors">
            {scraping ? 'Scraping...' : 'Scrape Now'}
          </button>
          <button onClick={handleScore} disabled={scoring}
            className="px-4 py-2 bg-border text-textPrimary text-sm font-medium rounded hover:bg-border/80 disabled:opacity-50 transition-colors">
            {scoring ? 'Scoring...' : 'Score Jobs'}
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-textSecondary text-center py-16">Loading stats...</div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            {statCards.map(({ label, value, color }) => (
              <div key={label} className="bg-card border border-border rounded-lg p-5">
                <p className="text-textSecondary text-sm">{label}</p>
                <p className={`text-3xl font-bold mt-1 ${color}`}>{value}</p>
              </div>
            ))}
          </div>

          <div className="bg-card border border-border rounded-lg p-6">
            <h2 className="text-textPrimary font-semibold mb-4">Match Distribution</h2>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={chartData} barSize={48}>
                <XAxis dataKey="name" tick={{ fill: '#9E9E9E', fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#9E9E9E', fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#1A1D2E', border: '1px solid #2A2D3E', borderRadius: 6 }}
                  labelStyle={{ color: '#E0E0E0' }}
                  itemStyle={{ color: '#9E9E9E' }}
                />
                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                  {chartData.map((entry, index) => (
                    <Cell key={index} fill={entry.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  )
}
