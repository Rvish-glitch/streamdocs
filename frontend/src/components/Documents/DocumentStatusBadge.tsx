import { Badge } from "@/components/ui/badge"
import type { JobStatus } from "@/types/documents"

export function DocumentStatusBadge({ status }: { status?: JobStatus | null }) {
  if (!status) return <Badge variant="secondary">No task</Badge>

  switch (status) {
    case "QUEUED":
      return <Badge variant="secondary">Queued</Badge>
    case "PROCESSING":
      return <Badge>Processing</Badge>
    case "COMPLETED":
      return <Badge variant="outline">Completed</Badge>
    case "FAILED":
      return <Badge variant="destructive">Failed</Badge>
    default:
      return <Badge variant="secondary">Unknown</Badge>
  }
}
