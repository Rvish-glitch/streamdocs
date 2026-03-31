import axios from "axios"

export const apiBaseUrl = import.meta.env.VITE_API_URL as string

export const http = axios.create({
  baseURL: apiBaseUrl,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token")
  if (token) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})
