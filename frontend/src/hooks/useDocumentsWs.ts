import { useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import type { DocumentDetail, DocumentsListResponse, JobStatus } from "@/types/documents"

function deriveWsBase(apiBaseUrl: string): string {
  if (apiBaseUrl.startsWith("https://")) return apiBaseUrl.replace("https://", "wss://")
  if (apiBaseUrl.startsWith("http://")) return apiBaseUrl.replace("http://", "ws://")
  return apiBaseUrl
}

export interface DocumentWsEvent {
  type: string
  job_id?: string
  document_id?: string
  document_ids?: string[]
  status?: JobStatus
  stage?: string
  progress?: number
  message?: string
  ts?: string
  [key: string]: any
}

export function useDocumentsWs() {
  const queryClient = useQueryClient()
  const [lastEvent, setLastEvent] = useState<DocumentWsEvent | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const socketRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)

  const token = localStorage.getItem("access_token")

  const wsUrl = useMemo(() => {
    if (!token) return null
    const apiBaseUrl = (import.meta.env.VITE_API_URL as string) || "http://localhost:8000"
    const wsBase = deriveWsBase(apiBaseUrl)
    return `${wsBase}/api/v1/documents/ws?token=${encodeURIComponent(token)}`
  }, [token])

  useEffect(() => {
    if (!wsUrl) return

    let isMounted = true

    function connect() {
      if (!isMounted) return

      try {
        const ws = new WebSocket(wsUrl!)
        socketRef.current = ws

        ws.onopen = () => {
          if (!isMounted) return
          setIsConnected(true)
        }

        ws.onclose = () => {
          if (!isMounted) return
          setIsConnected(false)
          // Attempt reconnection after 3 seconds
          reconnectTimerRef.current = window.setTimeout(() => {
            connect()
          }, 3000)
        }

        ws.onerror = () => {
          if (!isMounted) return
          setIsConnected(false)
        }

        ws.onmessage = (msg) => {
          if (!isMounted) return
          try {
            const parsed = JSON.parse(msg.data) as DocumentWsEvent
            setLastEvent(parsed)

            // When worker reports stage/progress, update the React cache directly in-memory
            // This achieves ZERO HTTP polling / GET requests while the worker processes!
            if (parsed.type === "job_progress") {
              queryClient.setQueriesData<DocumentsListResponse>(
                { queryKey: ["documents"] },
                (old) => {
                  if (!old?.data) return old
                  return {
                    ...old,
                    data: old.data.map((doc) => {
                      if (
                        doc.id === parsed.document_id ||
                        (parsed.job_id && doc.latest_job?.id === parsed.job_id)
                      ) {
                        return {
                          ...doc,
                          latest_job: {
                            id: parsed.job_id || doc.latest_job?.id || "",
                            document_id: doc.id,
                            status: parsed.status || doc.latest_job?.status || "PROCESSING",
                            progress: parsed.progress ?? doc.latest_job?.progress ?? 0,
                            current_stage: parsed.stage ?? doc.latest_job?.current_stage,
                          },
                        }
                      }
                      return doc
                    }),
                  }
                }
              )

              if (parsed.document_id) {
                queryClient.setQueryData<DocumentDetail>(
                  ["document", parsed.document_id],
                  (old) => {
                    if (!old) return old
                    return {
                      ...old,
                      latest_job: {
                        id: parsed.job_id || old.latest_job?.id || "",
                        document_id: old.id,
                        status: parsed.status || old.latest_job?.status || "PROCESSING",
                        progress: parsed.progress ?? old.latest_job?.progress ?? 0,
                        current_stage: parsed.stage ?? old.latest_job?.current_stage,
                      },
                    }
                  }
                )
              }

              // When the job completes, sync final extracted JSON data once
              if (parsed.status === "COMPLETED" || parsed.status === "FAILED") {
                queryClient.invalidateQueries({ queryKey: ["documents"] })
                if (parsed.document_id) {
                  queryClient.invalidateQueries({ queryKey: ["document", parsed.document_id] })
                }
              }
            } else if (
              parsed.type === "document_uploaded" ||
              parsed.type === "document_deleted" ||
              parsed.type === "document_updated" ||
              parsed.type === "document_reprocessed" ||
              parsed.type === "job_deleted"
            ) {
              queryClient.invalidateQueries({ queryKey: ["documents"] })
              if (parsed.document_id) {
                queryClient.invalidateQueries({ queryKey: ["document", parsed.document_id] })
              }
            }
          } catch {
            // Ignore malformed message
          }
        }
      } catch {
        setIsConnected(false)
      }
    }

    connect()

    return () => {
      isMounted = false
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
      }
      if (socketRef.current) {
        socketRef.current.close()
        socketRef.current = null
      }
    }
  }, [wsUrl, queryClient])

  return { isConnected, lastEvent }
}
