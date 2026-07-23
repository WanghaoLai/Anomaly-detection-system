const configuredBaseUrl = new URL(
  import.meta.env.VITE_BASE_URL,
  window.location.origin,
)

// 本地开发时让 API 与当前页面使用同一主机名，避免 localhost 与
// 127.0.0.1 被浏览器视为不同站点而拒绝 SameSite Cookie。
if (
  ['localhost', '127.0.0.1'].includes(window.location.hostname)
  && ['localhost', '127.0.0.1'].includes(configuredBaseUrl.hostname)
) {
  configuredBaseUrl.hostname = window.location.hostname
}

export const API_BASE_URL = configuredBaseUrl.toString().replace(/\/$/, '')

export const getCookie = (name) => {
  const prefix = `${encodeURIComponent(name)}=`
  const item = document.cookie
    .split('; ')
    .find(cookie => cookie.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : ''
}

export const getCsrfToken = () => getCookie('csrf_token')

export const saveAuthenticatedUser = (payload) => {
  const user = payload?.data?.user
  if (user) {
    localStorage.setItem('system-user', JSON.stringify(user))
  }
  // 清理旧版本遗留的可读 Access Token。
  localStorage.removeItem('token')
}

export const clearAuthState = () => {
  localStorage.removeItem('token')
  localStorage.removeItem('system-user')
}
