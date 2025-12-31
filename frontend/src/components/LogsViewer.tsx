import { useEffect, useState, useCallback, memo } from 'react'
import './LogsViewer.css'

interface LogEntry {
  timestamp?: string
  type?: string
  [key: string]: any
}

function LogsViewer() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<string>('')
  const [methodFilter, setMethodFilter] = useState<string>('')
  const [searchQuery, setSearchQuery] = useState<string>('')
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState<string>('')
  const [offset, setOffset] = useState(0)
  const [limit] = useState(1000)
  const [collapsedEntries, setCollapsedEntries] = useState<Set<number>>(new Set())

  // Debounce search query
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery)
      setOffset(0) // Reset offset when search changes
    }, 300) // 300ms debounce

    return () => clearTimeout(timer)
  }, [searchQuery])

  const loadLogs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        limit: limit.toString(),
        offset: offset.toString(),
      })
      if (typeFilter) params.append('type', typeFilter)
      if (methodFilter) params.append('method', methodFilter)
      if (debouncedSearchQuery) params.append('search', debouncedSearchQuery)

      const response = await fetch(`/api/logs?${params}`)
      const data = await response.json()

      if (data.success) {
        setLogs(data.logs || [])
      } else {
        setError(data.error || 'Failed to load logs')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load logs')
    } finally {
      setLoading(false)
    }
  }, [typeFilter, methodFilter, debouncedSearchQuery, offset, limit])

  useEffect(() => {
    loadLogs()
  }, [loadLogs])

  const toggleCollapse = useCallback((index: number) => {
    setCollapsedEntries(prev => {
      const newCollapsed = new Set(prev)
      if (newCollapsed.has(index)) {
        newCollapsed.delete(index)
      } else {
        newCollapsed.add(index)
      }
      return newCollapsed
    })
  }, [])

  const clearFilters = useCallback(() => {
    setTypeFilter('')
    setMethodFilter('')
    setSearchQuery('')
    setOffset(0)
  }, [])

  const getLogTypeClass = useCallback((type: string = '') => {
    return `log-type ${type.toLowerCase().replace('_', '-')}`
  }, [])

  const formatLogContent = useCallback((entry: LogEntry) => {
    const entries = Object.entries(entry)
      .filter(([key]) => key !== 'timestamp' && key !== 'type')
      .map(([key, value]) => ({
        key,
        value: typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value),
        isJson: typeof value === 'object',
      }))
    return entries
  }, [])

  return (
    <div className="logs-viewer">
      <div className="logs-header">
        <h1>📝 Activity Logs Viewer</h1>
        <div className="logs-controls">
          <div className="control-group">
            <label>Type:</label>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">All Types</option>
              <option value="request_response">Request/Response</option>
              <option value="cookies">Cookies</option>
              <option value="storage">Storage</option>
              <option value="console">Console</option>
              <option value="interaction">Interactions</option>
            </select>
          </div>
          <div className="control-group">
            <label>Method:</label>
            <select
              value={methodFilter}
              onChange={(e) => setMethodFilter(e.target.value)}
            >
              <option value="">All Methods</option>
              <option value="GET">GET</option>
              <option value="POST">POST</option>
              <option value="PUT">PUT</option>
              <option value="PATCH">PATCH</option>
              <option value="DELETE">DELETE</option>
              <option value="OPTIONS">OPTIONS</option>
              <option value="HEAD">HEAD</option>
            </select>
          </div>
          <div className="control-group">
            <label>Search:</label>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search in logs..."
              style={{ width: '200px' }}
            />
          </div>
          <button onClick={loadLogs}>Refresh</button>
          <button onClick={clearFilters}>Clear Filters</button>
        </div>
        <div className="logs-stats">
          <div className="stat">
            <div className="stat-label">Total Logs</div>
            <div className="stat-value">{logs.length}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Filtered</div>
            <div className="stat-value">{logs.length}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Showing</div>
            <div className="stat-value">{logs.length}</div>
          </div>
        </div>
      </div>

      {error && (
        <div className="error-message">{error}</div>
      )}

      <div className="logs-container">
        {loading ? (
          <div className="loading">Loading logs...</div>
        ) : logs.length === 0 ? (
          <div className="empty-state">No logs found</div>
        ) : (
          logs.map((entry, index) => (
            <div
              key={index}
              className={`log-entry ${collapsedEntries.has(index) ? 'collapsed' : ''}`}
            >
              <div
                className="log-header"
                onClick={() => toggleCollapse(index)}
              >
                <span className={getLogTypeClass(entry.type)}>
                  {entry.type || 'unknown'}
                </span>
                <span className="log-timestamp">
                  {entry.timestamp || 'No timestamp'}
                </span>
              </div>
              <div className="log-content">
                {formatLogContent(entry).map((item, idx) => (
                  <div key={idx} className="log-field">
                    <div className="log-field-label">{item.key}</div>
                    <div className={`log-field-value ${item.isJson ? 'json' : ''}`}>
                      {item.value}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default memo(LogsViewer)

