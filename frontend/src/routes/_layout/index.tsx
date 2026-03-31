import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link as RouterLink, createFileRoute } from "@tanstack/react-router"
import { Search } from "lucide-react"

import { DocumentsApi } from "@/api/documents"
import { DataTable } from "@/components/Common/DataTable"
import { UploadDocuments } from "@/components/Documents/UploadDocuments"
import { DocumentStatusBadge } from "@/components/Documents/DocumentStatusBadge"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import type { ColumnDef } from "@tanstack/react-table"
import type { DocumentListItem, JobStatus } from "@/types/documents"

export const Route = createFileRoute("/_layout/")({
  component: DocumentsDashboard,
  head: () => ({
    meta: [
      {
        title: "Documents - StreamDocs",
      },
    ],
  }),
})

function DocumentsDashboard() {
  const [q, setQ] = useState("")
  const [status, setStatus] = useState<JobStatus | "ALL">("ALL")
  const [sort, setSort] = useState<"created_at" | "filename">("created_at")
  const [order, setOrder] = useState<"desc" | "asc">("desc")

  const { data, isLoading } = useQuery({
    queryKey: ["documents", { q, status, sort, order }],
    queryFn: () =>
      DocumentsApi.list({
        q: q || undefined,
        status: status === "ALL" ? undefined : status,
        sort,
        order,
        skip: 0,
        limit: 100,
      }),
    refetchInterval: 1000,
  })

  const documents = data?.data ?? []

  const filtered = useMemo(() => {
    if (!q) return documents
    const query = q.toLowerCase().trim()
    return documents.filter((d) => d.original_filename.toLowerCase().includes(query))
  }, [documents, q])

  const columns = useMemo<ColumnDef<DocumentListItem>[]>(
    () => [
      {
        header: "Filename",
        cell: ({ row }) => (
          <RouterLink
            to="/documents/$documentId"
            params={{ documentId: row.original.id }}
            className="underline underline-offset-4"
          >
            {row.original.original_filename}
          </RouterLink>
        ),
      },
      {
        header: "Status",
        cell: ({ row }) => (
          <DocumentStatusBadge status={row.original.latest_job?.status} />
        ),
      },
      {
        header: "Progress",
        cell: ({ row }) => (
          <div className="flex items-center gap-3 min-w-48">
            {row.original.latest_job ? (
              <>
                <Progress value={row.original.latest_job.progress ?? 0} />
                <span className="text-xs text-muted-foreground w-10 text-right">
                  {Number.isInteger(row.original.latest_job.progress ?? 0)
                    ? (row.original.latest_job.progress ?? 0)
                    : (row.original.latest_job.progress ?? 0).toFixed(1)}
                  %
                </span>
              </>
            ) : (
              <span className="text-xs text-muted-foreground">—</span>
            )}
          </div>
        ),
      },
      {
        header: "Updated",
        cell: ({ row }) => (
          <span className="text-sm text-muted-foreground">
            {row.original.latest_job?.finished_at || row.original.created_at || ""}
          </span>
        ),
      },
    ],
    [],
  )

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold tracking-tight">Documents</h1>
        <p className="text-muted-foreground">
          Upload documents, track processing progress, and finalize results.
        </p>
      </div>

      <UploadDocuments />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        <div className="md:col-span-2">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search by filename"
              className="pl-9"
            />
          </div>
        </div>

        <Select value={status} onValueChange={(v) => setStatus(v as any)}>
          <SelectTrigger>
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All statuses</SelectItem>
            <SelectItem value="QUEUED">Queued</SelectItem>
            <SelectItem value="PROCESSING">Processing</SelectItem>
            <SelectItem value="COMPLETED">Completed</SelectItem>
            <SelectItem value="FAILED">Failed</SelectItem>
          </SelectContent>
        </Select>

        <div className="flex gap-2">
          <Select value={sort} onValueChange={(v) => setSort(v as any)}>
            <SelectTrigger>
              <SelectValue placeholder="Sort" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="created_at">Created</SelectItem>
              <SelectItem value="filename">Filename</SelectItem>
            </SelectContent>
          </Select>
          <Select value={order} onValueChange={(v) => setOrder(v as any)}>
            <SelectTrigger>
              <SelectValue placeholder="Order" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="desc">Desc</SelectItem>
              <SelectItem value="asc">Asc</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {isLoading ? (
        <div className="text-sm text-muted-foreground">Loading…</div>
      ) : (
        <DataTable columns={columns} data={filtered} />
      )}

      <div className="text-xs text-muted-foreground">
        Click a filename to review/edit results.
      </div>
    </div>
  )
}

