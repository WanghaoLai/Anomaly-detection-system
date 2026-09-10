import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'
import {
  API_BASE_URL,
  clearAuthState,
  getCsrfToken,
  saveAuthenticatedUser,
} from './auth'


const request = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
})

const refreshClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
})

let refreshPromise = null

const refreshSession = () => {
  if (!refreshPromise) {
    refreshPromise = refreshClient.post('/refresh', {}, {
      headers: { 'X-CSRF-Token': getCsrfToken() },
    }).then(response => {
      saveAuthenticatedUser(response.data)
      return response
    }).finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

const isAuthEndpoint = url => ['/login', '/refresh', '/logout', '/verify']
  .some(path => String(url).endsWith(path))

const handleTerminalUnauthorized = url => {
  const hadAuthenticatedUser = Boolean(localStorage.getItem('system-user'))
  clearAuthState()
  if (
    hadAuthenticatedUser
    && !isAuthEndpoint(url)
    && router.currentRoute.value.path !== '/login'
  ) {
    ElMessage.error('登录已过期，请重新登录')
  }
  if (router.currentRoute.value.path !== '/login') {
    router.push('/login')
  }
}

/**
 * 为 Fetch/SSE 请求提供与 Axios 完全相同的 Cookie、CSRF、单飞刷新和失效处理。
 */
export const authenticatedFetch = async (path, options = {}) => {
  const url = /^https?:\/\//i.test(path)
    ? path
    : `${API_BASE_URL.replace(/\/$/, '')}/${String(path).replace(/^\//, '')}`
  const requestOptions = { ...options, credentials: 'include' }
  const method = String(requestOptions.method || 'GET').toUpperCase()
  const headers = new Headers(requestOptions.headers || {})
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    headers.set('X-CSRF-Token', getCsrfToken())
  }
  requestOptions.headers = headers

  let response = await fetch(url, requestOptions)
  const canRefresh = (
    response.status === 401
    && Boolean(getCsrfToken())
    && !isAuthEndpoint(url)
  )
  if (canRefresh) {
    try {
      await refreshSession()
      const retryHeaders = new Headers(requestOptions.headers)
      retryHeaders.set('X-CSRF-Token', getCsrfToken())
      response = await fetch(url, { ...requestOptions, headers: retryHeaders })
    } catch {
      // 刷新失败由下面的统一终态处理收口。
    }
  }
  if (response.status === 401) {
    handleTerminalUnauthorized(url)
  }
  return response
}

request.interceptors.request.use(config => {
  const method = String(config.method || 'get').toUpperCase()
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    config.headers['X-CSRF-Token'] = getCsrfToken()
  }
  return config
})

request.interceptors.response.use(
  response => {
    let res = response.data
    if (response.config.responseType === 'blob') {
      return res
    }
    if (typeof res === 'string') {
      res = res ? JSON.parse(res) : res
    }
    return res
  },
  async error => {
    const original = error.config || {}
    const url = String(original.url || '')
    const canRefresh = (
      error.response?.status === 401
      && !original._retry
      && Boolean(getCsrfToken())
      && !['/login', '/refresh', '/logout'].some(path => url.endsWith(path))
    )

    if (canRefresh) {
      original._retry = true
      try {
        await refreshSession()
        return request(original)
      } catch {
        // 统一进入下面的会话清理流程。
      }
    }

    if (error.response?.status === 401) {
      handleTerminalUnauthorized(url)
    }
    const backendMessage = error.response?.data?.msg || error.response?.data?.detail
    if (backendMessage) error.message = backendMessage
    return Promise.reject(error)
  },
)


export default request
