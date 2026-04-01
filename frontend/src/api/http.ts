import axios from "axios"

export const apiBaseUrl = import.meta.env.VITE_API_URL as string

// If VITE_API_URL is unset/empty, fall back to same-origin.
// All callers use absolute paths like /api/v1/..., so baseURL should be "".
export const resolvedApiBaseUrl = apiBaseUrl || ""

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
