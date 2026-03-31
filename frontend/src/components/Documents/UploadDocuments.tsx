import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"

import { DocumentsApi } from "@/api/documents"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { toast } from "sonner"

export function UploadDocuments() {
  const [files, setFiles] = useState<File[]>([])
  const queryClient = useQueryClient()

  const uploadMutation = useMutation({
    mutationFn: () => DocumentsApi.upload(files),
    onSuccess: async () => {
      toast.success("Upload started")
      setFiles([])
      await queryClient.invalidateQueries({ queryKey: ["documents"] })
    },
    onError: () => {
      toast.error("Upload failed")
    },
  })

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
      <Input
        type="file"
        multiple
        onChange={(e) => {
          const list = e.target.files
          setFiles(list ? Array.from(list) : [])
        }}
      />
      <Button
        onClick={() => uploadMutation.mutate()}
        disabled={files.length === 0 || uploadMutation.isPending}
      >
        Upload
      </Button>
    </div>
  )
}
