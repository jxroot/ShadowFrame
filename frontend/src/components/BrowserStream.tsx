import { useCallback, useEffect, useRef, useState, memo } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'
import './BrowserStream.css'

function BrowserStream() {
  const { isConnected, sendMessage, lastMessage } = useWebSocket('/ws')
  const [isInteracting, setIsInteracting] = useState(false)
  const [viewport, setViewport] = useState({ width: 1024, height: 768 })
  const frameRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const lastFrameIdRef = useRef<number>(-1)
  const scrollThrottleRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingScrollRef = useRef<{ deltaX: number; deltaY: number } | null>(null)
  const rafRef = useRef<number | null>(null)

  const handleClick = useCallback(async (event: MouseEvent) => {
    if (isInteracting || !imgRef.current) return
    setIsInteracting(true)

    const img = event.target as HTMLImageElement
    const rect = img.getBoundingClientRect()
    const x = ((event.clientX - rect.left) * viewport.width) / rect.width
    const y = ((event.clientY - rect.top) * viewport.height) / rect.height

    sendMessage({
      action: 'click',
      x: x,
      y: y,
    })

      setTimeout(() => {
        setIsInteracting(false)
      }, 100)
  }, [isInteracting, sendMessage, viewport])

  const updateFrame = useCallback((data: { f?: string; fmt?: string; id?: number }) => {
    if (!frameRef.current) return

    // Frame skipping: Skip old frames if client is slow
    if (data.id !== undefined) {
      if (data.id <= lastFrameIdRef.current) {
        // Old frame, skip it
        return
      }
      lastFrameIdRef.current = data.id
    }

    // Regular screenshot display
    if (!imgRef.current && frameRef.current) {
      const img = document.createElement('img')
      img.className = 'browser-stream'
      img.alt = 'Browser stream'
      frameRef.current.innerHTML = ''
      frameRef.current.appendChild(img)
      
      img.addEventListener('click', handleClick)
      img.addEventListener('contextmenu', (e) => {
        e.preventDefault()
        handleClick(e)
      })
      
      imgRef.current = img
    }

    // Use requestAnimationFrame for smooth updates
    if (imgRef.current && data.f) {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
      }
      
      rafRef.current = requestAnimationFrame(() => {
        if (imgRef.current && data.f) {
          const format = data.fmt || 'jpeg'
          imgRef.current.src = `data:image/${format};base64,${data.f}`
        }
      })
    }
  }, [handleClick])

  useEffect(() => {
    if (lastMessage) {
      if (lastMessage.type === 'init') {
        // Initial connection - navigate to default URL if provided
        console.log('Initialized:', lastMessage.info)
        // Update viewport from backend config
        if (lastMessage.viewport) {
          setViewport(lastMessage.viewport)
        }
        if (lastMessage.default_url && lastMessage.default_url.trim() !== '') {
          // Navigate to default URL after a short delay
          setTimeout(() => {
            sendMessage({
              action: 'navigate',
              url: lastMessage.default_url,
            })
          }, 500)
        }
      } else if (lastMessage.f) {
        // Frame data received
        updateFrame(lastMessage)
      } else if (lastMessage.type === 'navigate_result') {
        if (!lastMessage.success) {
          alert('Navigation failed')
        }
      }
    }
  }, [lastMessage, updateFrame, sendMessage])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      
      // Check if user is in an input field, textarea, or contenteditable element
      const isInputElement = 
        target.tagName === 'INPUT' || 
        target.tagName === 'TEXTAREA' || 
        target.isContentEditable ||
        target.closest('input, textarea, [contenteditable]')
      
      // If user is in an input field, allow all keyboard shortcuts to work normally
      // This includes Ctrl+A, Ctrl+V, Ctrl+C, Ctrl+X, Ctrl+Z, etc.
      if (isInputElement) {
        // Only intercept if it's a special browser shortcut (Ctrl+L for navigate)
        if ((e.ctrlKey || e.metaKey) && e.key === 'l') {
          e.preventDefault()
          const url = prompt('Enter URL to navigate:')
          if (url && url.trim()) {
            sendMessage({ action: 'navigate', url: url.trim() })
          }
        }
        // For all other keys in input fields, let them work normally
        return
      }

      // For non-input elements, handle browser control shortcuts
      // Keyboard shortcuts
      if ((e.ctrlKey || e.metaKey) && e.key === 'l') {
        // Ctrl+L or Cmd+L: Focus URL bar (navigate)
        e.preventDefault()
        const url = prompt('Enter URL to navigate:')
        if (url && url.trim()) {
          sendMessage({ action: 'navigate', url: url.trim() })
        }
        return
      }

      // Handle special keys for browser control
      if (e.key === 'Enter') {
        e.preventDefault()
        sendMessage({ action: 'key', key: 'Enter' })
      } else if (e.key === 'Escape') {
        e.preventDefault()
        sendMessage({ action: 'key', key: 'Escape' })
      } else if (e.key === 'Backspace') {
        e.preventDefault()
        sendMessage({ action: 'key', key: 'Backspace' })
      } else if (e.key === 'Tab') {
        e.preventDefault()
        sendMessage({ action: 'key', key: 'Tab' })
      } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        // Only send single character keys if no modifier keys are pressed
        e.preventDefault()
        sendMessage({ action: 'type', text: e.key })
      }
    }

    const handleWheel = (e: WheelEvent) => {
      if (!frameRef.current) return
      e.preventDefault()
      
      // Accumulate scroll delta
      if (pendingScrollRef.current) {
        pendingScrollRef.current.deltaX += e.deltaX
        pendingScrollRef.current.deltaY += e.deltaY
      } else {
        pendingScrollRef.current = { deltaX: e.deltaX, deltaY: e.deltaY }
      }
      
      // Throttle scroll events (send every 50ms max)
      if (!scrollThrottleRef.current) {
        scrollThrottleRef.current = setTimeout(() => {
          if (pendingScrollRef.current) {
            sendMessage({
              action: 'scroll',
              deltaX: pendingScrollRef.current.deltaX,
              deltaY: pendingScrollRef.current.deltaY,
            })
            pendingScrollRef.current = null
          }
          scrollThrottleRef.current = null
        }, 50) // 50ms throttle = max 20 scroll events per second
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    const frame = frameRef.current
    if (frame) {
      frame.addEventListener('wheel', handleWheel)
    }

    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (frame) {
        frame.removeEventListener('wheel', handleWheel)
      }
      if (scrollThrottleRef.current) {
        clearTimeout(scrollThrottleRef.current)
      }
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
      }
    }
  }, [sendMessage])

  return (
    <div className="browser-stream-container">
      <div className="browser-frame" ref={frameRef}>
        {!isConnected && (
          <div className="loading">Connecting to stream...</div>
        )}
        {isConnected && !lastMessage && (
          <div className="loading">Waiting for stream...</div>
        )}
      </div>
      {/* Connection status indicator */}
      <div className={`connection-status ${isConnected ? 'connected' : 'disconnected'}`}>
        <span className="status-dot"></span>
        <span className="status-text">{isConnected ? 'Connected' : 'Disconnected'}</span>
      </div>
    </div>
  )
}

export default memo(BrowserStream)

