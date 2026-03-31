import { useEffect, useMemo, useRef, useState } from "react"
import type { JobProgressEvent } from "@/types/documents"

function deriveWsBase(apiBaseUrl: string): string {
  // http(s)://host -> ws(s)://host
  if (apiBaseUrl.startsWith("https://")) return apiBaseUrl.replace("https://", "wss://")
  if (apiBaseUrl.startsWith("http://")) return apiBaseUrl.replace("http://", "ws://")
  return apiBaseUrl
}

export function useJobProgress(jobId?: string | null) {
  const [lastEvent, setLastEvent] = useState<JobProgressEvent | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    setLastEvent(null)
    setIsConnected(false)
  }, [jobId])

  const wsUrl = useMemo(() => {
    if (!jobId) return null
    const token = localStorage.getItem("access_token")
    if (!token) return null
    const apiBaseUrl = import.meta.env.VITE_API_URL as string
    const wsBase = deriveWsBase(apiBaseUrl)
    return `${wsBase}/api/v1/jobs/${jobId}/ws?token=${encodeURIComponent(token)}`
  }, [jobId])

  useEffect(() => {
    if (!wsUrl) return

    const ws = new WebSocket(wsUrl)
    socketRef.current = ws

    ws.onopen = () => setIsConnected(true)
    ws.onclose = () => setIsConnected(false)
    ws.onerror = () => setIsConnected(false)
    ws.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data) as JobProgressEvent
        setLastEvent(parsed)
      } catch {
        // ignore malformed
      }
    }

    return () => {
      ws.close()
      socketRef.current = null
    }
  }, [wsUrl])

  return { lastEvent, isConnected }
}
