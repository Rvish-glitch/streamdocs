export type JobStatus = "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED"
export type ReviewStatus = "DRAFT" | "FINAL"

export type ProcessingJob = {
  id: string
  document_id: string
  status: JobStatus
  progress: number
  current_stage?: string | null
  error_message?: string | null
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
}

export type ExtractionResult = {
  id: string
  document_id: string
  job_id: string
  extracted_json: Record<string, unknown>
  review_status: ReviewStatus
  finalized_at?: string | null
}

export type DocumentListItem = {
  id: string
  original_filename: string
  content_type?: string | null
  size_bytes?: number
  created_at?: string
  latest_job?: ProcessingJob | null
  result?: Pick<ExtractionResult, "review_status"> | null
}

export type DocumentsListResponse = {
  data: DocumentListItem[]
  count: number
}

export type DocumentDetail = {
  id: string
  original_filename: string
  content_type?: string | null
  size_bytes?: number
  created_at?: string
  latest_job?: ProcessingJob | null
  result?: ExtractionResult | null
}

export type JobProgressEvent = {
  job_id: string
  document_id?: string
  status?: JobStatus
  stage?: string
  progress?: number
  ts?: string
  message?: string
}
