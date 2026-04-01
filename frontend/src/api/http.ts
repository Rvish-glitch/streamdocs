import axios from "axios"

export const apiBaseUrl = import.meta.env.VITE_API_URL as string

const normalizeApiBaseUrl = (value: string | undefined): string => {
  const raw = (value || "").trim()
  if (!raw) return ""

  // Our callers already use absolute paths like /api/v1/...
  // If someone sets VITE_API_URL to '/api' or '/api/v1', strip it to avoid
  // generating URLs like /api/v1/api/v1/...
  return raw.replace(/\/api(\/v1)?\/?$/, "")
}

export const resolvedApiBaseUrl = normalizeApiBaseUrl(apiBaseUrl)

export const http = axios.create({
  baseURL: resolvedApiBaseUrl,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token")
  if (token) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})
