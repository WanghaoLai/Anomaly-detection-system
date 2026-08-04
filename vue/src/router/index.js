import { createRouter, createWebHistory } from 'vue-router'
import axios from 'axios'
import {
  API_BASE_URL,
  clearAuthState,
  getCsrfToken,
  saveAuthenticatedUser,
} from '@/utils/auth'


const WHITE_LIST = ['/login', '/register']
let sessionVerified = false

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/login' },
    {
      path: '/manager',
      component: () => import('@/views/Manager.vue'),
      redirect: '/manager/home',
      children: [
        { path: 'person', component: () => import('@/views/manager/Person.vue') },
        { path: 'password', component: () => import('@/views/manager/Password.vue') },
        { path: 'home', component: () => import('@/views/manager/Home.vue') },
        { path: 'admin', component: () => import('@/views/manager/Admin.vue') },
        { path: 'user', component: () => import('@/views/manager/User.vue') },
        { path: 'address', component: () => import('@/views/manager/Address.vue') },
        { path: 'notice', component: () => import('@/views/manager/Notice.vue') },
        { path: 'chat', component: () => import('@/views/manager/Chat.vue') },
        { path: 'adminChat', component: () => import('@/views/manager/AdminChat.vue') },
        { path: 'knowledge', component: () => import('@/views/manager/Knowledge.vue') },
        { path: 'datasetInfo', component: () => import('@/views/manager/DatasetInfo.vue') },
        { path: 'datasetInfoAdmin', component: () => import('@/views/manager/DatasetInfoAdmin.vue') },
        { path: 'algorithmInfo', component: () => import('@/views/manager/AlgorithmInfo.vue') },
        { path: 'algorithmInfoAdmin', component: () => import('@/views/manager/AlgorithmInfoAdmin.vue') },
        { path: 'serverInfo', component: () => import('@/views/manager/ServerInfo.vue') },
        { path: 'datasetAdmin', component: () => import('@/views/manager/DatasetAdmin.vue') },
        { path: 'algorithmAdmin', component: () => import('@/views/manager/AlgorithmAdmin.vue') },
        { path: 'trainingJobs', component: () => import('@/views/manager/TrainingJobs.vue') },
        { path: 'inferenceJobs', component: () => import('@/views/manager/InferenceJobs.vue') },
        { path: 'experimentResults', component: () => import('@/views/manager/ExperimentResults.vue') },
        { path: 'upload', component: () => import('@/views/manager/Upload.vue') },
      ],
    },
    { path: '/login', component: () => import('@/views/Login.vue') },
    { path: '/register', component: () => import('@/views/Register.vue') },
  ],
})

const sessionClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
})

const verifySession = async () => {
  try {
    const response = await sessionClient.get('/verify')
    saveAuthenticatedUser(response.data)
  } catch (error) {
    if (error.response?.status !== 401) throw error
    // Refresh Token 使用双提交 CSRF 防护；缺少可读 CSRF Cookie 时，
    // 刷新请求必然失败，应直接按未登录处理。
    if (!getCsrfToken()) throw error
    await sessionClient.post('/refresh', {}, {
      headers: { 'X-CSRF-Token': getCsrfToken() },
    })
    const response = await sessionClient.get('/verify')
    saveAuthenticatedUser(response.data)
  }
}

router.beforeEach(async (to, from, next) => {
  if (to.path === '/register') {
    next()
    return
  }

  const cachedUser = localStorage.getItem('system-user')
  if (!cachedUser) sessionVerified = false

  if (!sessionVerified) {
    // 登录页没有本地用户提示时无需额外请求；受保护页面始终验证 Cookie。
    if (to.path === '/login' && !cachedUser) {
      clearAuthState()
      next()
      return
    }
    try {
      await verifySession()
      sessionVerified = true
    } catch {
      clearAuthState()
      sessionVerified = false
      next(WHITE_LIST.includes(to.path) ? undefined : '/login')
      return
    }
  }

  if (to.path === '/login') {
    next('/manager/home')
  } else {
    next()
  }
})


export default router
