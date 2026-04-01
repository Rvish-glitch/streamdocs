import { http } from "./http"
import type {
  DocumentDetail,
  DocumentsListResponse,
  ExtractionResult,
  JobStatus,
  ProcessingJob,
} from "@/types/documents"

export type ListDocumentsParams = {
  q?: string
  status?: JobStatus
  sort?: "created_at" | "filename"
  order?: "asc" | "desc"
  skip?: number
  limit?: number
}

export const DocumentsApi = {
  async list(params: ListDocumentsParams) {
    const { data } = await http.get<DocumentsListResponse>("/api/v1/documents/", {
      params,
    })
    return data
  },

  async upload(files: File[]) {
    const form = new FormData()
    for (const file of files) form.append("files", file)
    const { data } = await http.post<{ documents: DocumentDetail[] }>(
      "/api/v1/documents/upload",
      form,
      {
        headers: { "Content-Type": "multipart/form-data" },
      },
    )
    return data
  },

  async detail(documentId: string) {
    const { data } = await http.get<DocumentDetail>(
      `/api/v1/documents/${documentId}`,
    )
    return data
  },

  async updateResult(documentId: string, extractedJson: Record<string, unknown>) {
    const { data } = await http.put<ExtractionResult>(
      `/api/v1/documents/${documentId}/result`,
      { extracted_json: extractedJson },
    )
    return data
  },

  async finalize(documentId: string) {
    const { data } = await http.post<ExtractionResult>(
      `/api/v1/documents/${documentId}/finalize`,
    )
    return data
  },

  async retryJob(jobId: string) {
    const { data } = await http.post<ProcessingJob>(`/api/v1/jobs/${jobId}/retry`)
    return data
  },

  async deleteJob(jobId: string) {
    await http.delete(`/api/v1/jobs/${jobId}`)
  },

  async exportRecord(documentId: string, format: "json" | "csv") {
    const response = await http.get(`/api/v1/documents/${documentId}/export`, {
      params: { format },
      responseType: format === "csv" ? "blob" : "json",
    })
    return response.data
  },

  async deleteDocument(documentId: string) {
    await http.delete(`/api/v1/documents/${documentId}`)
  },
}
