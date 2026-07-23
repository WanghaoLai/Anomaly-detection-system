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
      const hadAuthenticatedUser = Boolean(localStorage.getItem('system-user'))
      const isAuthEndpoint = ['/login', '/refresh', '/logout', '/verify']
        .some(path => url.endsWith(path))
      clearAuthState()
      if (
        hadAuthenticatedUser
        && !isAuthEndpoint
        && router.currentRoute.value.path !== '/login'
      ) {
        ElMessage.error('登录已过期，请重新登录')
      }
      if (router.currentRoute.value.path !== '/login') {
        router.push('/login')
      }
    }
    return Promise.reject(error)
  },
)


export default request
