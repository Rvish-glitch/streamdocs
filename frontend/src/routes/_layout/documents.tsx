import { createFileRoute, Outlet } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/documents")({
  component: DocumentsLayout,
  head: () => ({
    meta: [{ title: "Documents - StreamDocs" }],
  }),
})

function DocumentsLayout() {
  return <Outlet />
}
