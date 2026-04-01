import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import {
  type Body_login_login_access_token as AccessToken,
  LoginService,
  type UserPublic,
  type UserRegister,
  UsersService,
} from "@/client"
import { handleError } from "@/utils"
import useCustomToast from "./useCustomToast"

const isLoggedIn = () => {
  const raw = localStorage.getItem("access_token")
  const token = raw?.trim()
  if (!token) {
    localStorage.removeItem("access_token")
    return false
  }

  // Our backend issues JWT access tokens. If the token is malformed or expired,
  // treat the user as logged out so the /login route can render.
  try {
    const parts = token.split(".")
    if (parts.length !== 3) {
      localStorage.removeItem("access_token")
      return false
    }

    const base64Url = parts[1]
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/")
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=")
    const json = atob(padded)
    const payload = JSON.parse(json) as { exp?: number }

    if (typeof payload.exp !== "number") {
      localStorage.removeItem("access_token")
      return false
    }

    // `exp` is seconds since epoch.
    const expiresAtMs = payload.exp * 1000
    if (Date.now() >= expiresAtMs) {
      localStorage.removeItem("access_token")
      return false
    }
  } catch {
    localStorage.removeItem("access_token")
    return false
  }

  return true
}

const useAuth = () => {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()

  const { data: user } = useQuery<UserPublic | null, Error>({
    queryKey: ["currentUser"],
    queryFn: UsersService.readUserMe,
    enabled: isLoggedIn(),
  })

  const signUpMutation = useMutation({
    mutationFn: (data: UserRegister) =>
      UsersService.registerUser({ requestBody: data }),
    onSuccess: () => {
      navigate({ to: "/login" })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })

  const login = async (data: AccessToken) => {
    const response = await LoginService.loginAccessToken({
      formData: data,
    })
    localStorage.setItem("access_token", response.access_token)
  }

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: () => {
      navigate({ to: "/" })
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = () => {
    localStorage.removeItem("access_token")
    navigate({ to: "/login" })
  }

  return {
    signUpMutation,
    loginMutation,
    logout,
    user,
  }
}

export { isLoggedIn }
export default useAuth
