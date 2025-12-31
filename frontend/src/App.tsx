import { useState, useCallback } from 'react'
import BrowserStream from './components/BrowserStream'
import LogsViewer from './components/LogsViewer'
import './App.css'

function App() {
  const [currentView, setCurrentView] = useState<'browser' | 'logs'>('browser')

  const switchToBrowser = useCallback(() => setCurrentView('browser'), [])
  const switchToLogs = useCallback(() => setCurrentView('logs'), [])

  return (
    <div className="app">
      <nav className="app-nav">
        <div className="nav-brand">MITM Browser</div>
        <div className="nav-links">
          <button
            className={currentView === 'browser' ? 'active' : ''}
            onClick={switchToBrowser}
          >
            Browser
          </button>
          <button
            className={currentView === 'logs' ? 'active' : ''}
            onClick={switchToLogs}
          >
            Logs
          </button>
        </div>
      </nav>
      <main className="app-main">
        {currentView === 'browser' ? <BrowserStream /> : <LogsViewer />}
      </main>
    </div>
  )
}

export default App

