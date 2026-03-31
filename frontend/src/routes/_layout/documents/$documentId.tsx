import type { ChangeEvent } from "react"
import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"

import { DocumentsApi } from "@/api/documents"
import { DocumentStatusBadge } from "@/components/Documents/DocumentStatusBadge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Textarea } from "@/components/ui/textarea"
import { useJobProgress } from "@/hooks/useJobProgress"
import type { JobStatus } from "@/types/documents"
import { toast } from "sonner"

export const Route = createFileRoute("/_layout/documents/$documentId")({
  component: DocumentDetail,
  head: () => ({
    meta: [{ title: "Document - StreamDocs" }],
  }),
})

function isTerminal(status?: JobStatus | null) {
  return status === "COMPLETED" || status === "FAILED"
}

function DocumentDetail() {
  const { documentId } = Route.useParams()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const { data, isLoading } = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => DocumentsApi.detail(documentId),
    refetchInterval: 1000,
  })

  const latestJob = data?.latest_job
  const { lastEvent, isConnected } = useJobProgress(latestJob?.id)

  const effectiveStatus = lastEvent?.status ?? latestJob?.status
  const effectiveProgress = lastEvent?.progress ?? latestJob?.progress ?? 0
  const effectiveStage = lastEvent?.stage ?? latestJob?.current_stage

  useEffect(() => {
    if (!lastEvent?.status) return
    if (lastEvent.status === "COMPLETED" || lastEvent.stage === "saved") {
      queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      queryClient.invalidateQueries({ queryKey: ["documents"] })
    }
    if (lastEvent.status === "FAILED") {
      queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      queryClient.invalidateQueries({ queryKey: ["documents"] })
    }
  }, [lastEvent?.status, lastEvent?.stage, documentId, queryClient])

  const [jsonText, setJsonText] = useState("")
  useEffect(() => {
    const extracted = data?.result?.extracted_json ?? {}
    setJsonText(JSON.stringify(extracted, null, 2))
  }, [data?.result?.extracted_json])

  const canRetry = effectiveStatus === "FAILED" && !!latestJob?.id
  const canDeleteJob =
    !!latestJob?.id &&
    (effectiveStatus === "FAILED" ||
      effectiveStatus === "COMPLETED" ||
      effectiveStatus === "QUEUED")
  const canEdit = effectiveStatus === "COMPLETED" && data?.result?.review_status !== "FINAL"
  const canFinalize = effectiveStatus === "COMPLETED" && data?.result?.review_status !== "FINAL"
  const canExport = data?.result?.review_status === "FINAL"

  const parsedJson = useMemo(() => {
    try {
      return JSON.parse(jsonText) as Record<string, unknown>
    } catch {
      return null
    }
  }, [jsonText])

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!parsedJson) throw new Error("Invalid JSON")
      return DocumentsApi.updateResult(documentId, parsedJson)
    },
    onSuccess: async () => {
      toast.success("Saved")
      await queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      await queryClient.invalidateQueries({ queryKey: ["documents"] })
    },
    onError: (e: any) => toast.error(e?.message || "Save failed"),
  })

  const finalizeMutation = useMutation({
    mutationFn: () => DocumentsApi.finalize(documentId),
    onSuccess: async () => {
      toast.success("Finalized")
      await queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      await queryClient.invalidateQueries({ queryKey: ["documents"] })
    },
    onError: () => toast.error("Finalize failed"),
  })

  const retryMutation = useMutation({
    mutationFn: () => DocumentsApi.retryJob(latestJob!.id),
    onSuccess: async () => {
      toast.success("Retry queued")
      await queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      await queryClient.invalidateQueries({ queryKey: ["documents"] })
    },
    onError: () => toast.error("Retry failed"),
  })

  const deleteJobMutation = useMutation({
    mutationFn: () => DocumentsApi.deleteJob(latestJob!.id),
    onSuccess: async () => {
      toast.success("Task deleted")
      await queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      await queryClient.invalidateQueries({ queryKey: ["documents"] })
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Delete failed"),
  })

  const exportJson = async () => {
    try {
      const exported = await DocumentsApi.exportRecord(documentId, "json")
      const blob = new Blob([JSON.stringify(exported, null, 2)], {
        type: "application/json",
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `${data?.original_filename || documentId}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error("Export failed")
    }
  }

  const exportCsv = async () => {
    try {
      const blob = (await DocumentsApi.exportRecord(documentId, "csv")) as Blob
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `${data?.original_filename || documentId}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error("Export failed")
    }
  }

  const deleteDocumentMutation = useMutation({
    mutationFn: () => DocumentsApi.deleteDocument(documentId),
    onSuccess: async () => {
      toast.success("Document deleted")
      await queryClient.invalidateQueries({ queryKey: ["documents"] })
      navigate({ to: "/" })
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Delete failed"),
  })

  if (isLoading) {
    return <div className="text-sm text-muted-foreground">Loading…</div>
  }

  if (!data) {
    return <div className="text-sm text-muted-foreground">Not found.</div>
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight break-all">
            {data.original_filename}
          </h1>
          <div className="mt-2 flex items-center gap-3">
            <DocumentStatusBadge status={effectiveStatus} />
            {!isTerminal(effectiveStatus) && (
              <span className="text-xs text-muted-foreground">
                {isConnected ? "Live" : "Connecting"}
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canRetry && (
            <Button
              variant="outline"
              onClick={() => retryMutation.mutate()}
              disabled={retryMutation.isPending}
            >
              Retry
            </Button>
          )}
          {canDeleteJob && (
            <Button
              variant="destructive"
              onClick={() => deleteJobMutation.mutate()}
              disabled={deleteJobMutation.isPending}
            >
              Delete task
            </Button>
          )}

          <Button
            variant="destructive"
            onClick={() => {
              const ok = window.confirm(
                "Delete this document permanently? This removes the file, tasks, and extracted result.",
              )
              if (!ok) return
              deleteDocumentMutation.mutate()
            }}
            disabled={deleteDocumentMutation.isPending}
          >
            Delete document
          </Button>
          {canExport && (
            <>
              <Button variant="outline" onClick={exportJson}>
                Export JSON
              </Button>
              <Button variant="outline" onClick={exportCsv}>
                Export CSV
              </Button>
            </>
          )}
        </div>
      </div>

      <Card className="p-4">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium">Progress</div>
            <div className="text-xs text-muted-foreground">
              {effectiveStage || ""}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Progress value={effectiveProgress} />
            <div className="w-10 text-right text-xs text-muted-foreground">
              {Number.isInteger(effectiveProgress)
                ? effectiveProgress
                : effectiveProgress.toFixed(1)}
              %
            </div>
          </div>
          {latestJob?.error_message && (
            <div className="text-sm text-destructive">{latestJob.error_message}</div>
          )}
        </div>
      </Card>

      <Card className="p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm font-medium">Extracted Output</div>
            <div className="text-xs text-muted-foreground">
              Review, edit, and finalize the structured result.
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              onClick={() => saveMutation.mutate()}
              disabled={!canEdit || saveMutation.isPending || !parsedJson}
            >
              Save edits
            </Button>
            <Button
              variant="outline"
              onClick={() => finalizeMutation.mutate()}
              disabled={!canFinalize || finalizeMutation.isPending}
            >
              Finalize
            </Button>
          </div>
        </div>

        <div className="mt-4">
          <Textarea
            value={jsonText}
            onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
              setJsonText(e.target.value)
            }
            disabled={!canEdit}
            spellCheck={false}
          />
          {!parsedJson && (
            <div className="mt-2 text-xs text-destructive">
              Invalid JSON (fix to enable saving).
            </div>
          )}
          {data.result?.review_status === "FINAL" && (
            <div className="mt-2 text-xs text-muted-foreground">
              This record is finalized.
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}
