import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import { AgentProvider } from './hooks/useAgent'
import Today from './pages/Today'
import Review from './pages/Review'
import Pipeline from './pages/Pipeline'
import Setup from './pages/Setup'

export default function App() {
  return (
    <BrowserRouter>
      <AgentProvider>
        <div className="h-full flex bg-bg text-text">
          <Sidebar />
          <main className="flex-1 min-w-0 h-full overflow-hidden">
            <Routes>
              <Route path="/" element={<Today />} />
              <Route path="/review" element={<Review />} />
              <Route path="/review/:jobId" element={<Review />} />
              <Route path="/pipeline" element={<Pipeline />} />
              <Route path="/setup" element={<Navigate to="/setup/you" replace />} />
              <Route path="/setup/:tab" element={<Setup />} />
              {/* The five old routes are gone; send any bookmark somewhere real. */}
              <Route path="/jobs" element={<Navigate to="/review" replace />} />
              <Route path="/applications" element={<Navigate to="/pipeline" replace />} />
              <Route path="/profile" element={<Navigate to="/setup/you" replace />} />
              <Route path="/settings" element={<Navigate to="/setup/boards" replace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </AgentProvider>
    </BrowserRouter>
  )
}
