import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Jobs from './pages/Jobs'
import Applications from './pages/Applications'
import Settings from './pages/Settings'

const NAV_LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/jobs', label: 'Jobs' },
  { to: '/applications', label: 'Applications' },
  { to: '/settings', label: 'Settings' },
]

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-bg flex">
        <aside className="w-56 bg-card border-r border-border flex flex-col py-8 px-4 fixed h-full">
          <div className="mb-10">
            <h1 className="text-accent text-lg font-bold">JobHunt Copilot</h1>
            <p className="text-textSecondary text-xs mt-1">AI-powered job search</p>
          </div>
          <nav className="flex flex-col gap-2">
            {NAV_LINKS.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  `px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-accent/10 text-accent'
                      : 'text-textSecondary hover:text-textPrimary hover:bg-border'
                  }`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </aside>
        <main className="ml-56 flex-1 p-8">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/applications" element={<Applications />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
