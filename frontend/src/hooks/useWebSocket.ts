import { useEffect, useRef, useState, useCallback } from 'react'

export interface WebSocketMessage {
    type?: string
    f?: string  // frame data (base64)
    fmt?: string  // format (jpeg/png)
    i?: any  // page info
    t?: number  // timestamp
    id?: number  // frame id
    success?: boolean  // for various result messages (navigate_result, etc.)
    info?: any
    default_url?: string | null  // default URL to navigate to
    viewport?: {
        width: number
        height: number
    }  // viewport dimensions from backend
}

export interface UseWebSocketReturn {
  ws: WebSocket | null
  isConnected: boolean
  sendMessage: (data: any) => void
  lastMessage: WebSocketMessage | null
}

export function useWebSocket(url: string): UseWebSocketReturn {
  const [ws, setWs] = useState<WebSocket | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState<WebSocketMessage | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const maxReconnectAttempts = 10

  const connect = useCallback(() => {
    try {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = url.startsWith('ws://') || url.startsWith('wss://') 
        ? url 
        : `${protocol}//${window.location.host}${url}`
      
      const websocket = new WebSocket(wsUrl)
      
      websocket.onopen = () => {
        console.log('✅ WebSocket connected')
        setIsConnected(true)
        setWs(websocket)
        reconnectAttemptsRef.current = 0
      }
      
      websocket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as WebSocketMessage
          setLastMessage(data)
        } catch (e) {
          console.error('Error parsing WebSocket message:', e)
        }
      }
      
      websocket.onerror = (error) => {
        console.error('WebSocket error:', error)
      }
      
      websocket.onclose = () => {
        console.log('WebSocket closed')
        setIsConnected(false)
        setWs(null)
        
        // Reconnect logic
        if (reconnectAttemptsRef.current < maxReconnectAttempts) {
          reconnectAttemptsRef.current++
          const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 10000)
          reconnectTimeoutRef.current = setTimeout(() => {
            console.log(`Reconnecting... (attempt ${reconnectAttemptsRef.current})`)
            connect()
          }, delay)
        } else {
          console.error('Max reconnection attempts reached')
        }
      }
    } catch (e) {
      console.error('WebSocket connection error:', e)
    }
  }, [url])

  useEffect(() => {
    connect()
    
    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
      if (ws) {
        ws.close()
      }
    }
  }, [connect])

  const sendMessage = useCallback((data: any) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data))
    } else {
      console.warn('WebSocket is not connected')
    }
  }, [ws])

  return {
    ws,
    isConnected,
    sendMessage,
    lastMessage,
  }
}

