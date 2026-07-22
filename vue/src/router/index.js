import {createRouter, createWebHistory} from 'vue-router'
import axios from 'axios'

const WHITE_LIST = ['/login', '/register']

// 标记 token 是否已通过后端验证，避免每次路由跳转都请求
let tokenVerified = false

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/login' },
    {
      path: '/manager',
      component: () => import('@/views/Manager.vue'),
      redirect: '/manager/home',
      children: [
        { path: 'person', component: () => import('@/views/manager/Person.vue')},
        { path: 'password', component: () => import('@/views/manager/Password.vue')},
        { path: 'home', component: () => import('@/views/manager/Home.vue')},
        { path: 'admin', component: () => import('@/views/manager/Admin.vue')},
        { path: 'user', component: () => import('@/views/manager/User.vue')},
        { path: 'address', component: () => import('@/views/manager/Address.vue')},
        { path: 'notice', component: () => import('@/views/manager/Notice.vue')},
        { path: 'chat', component: () => import('@/views/manager/Chat.vue')},
        { path: 'knowledge', component: () => import('@/views/manager/Knowledge.vue')},
        { path: 'datasetInfo', component: () => import('@/views/manager/DatasetInfo.vue')},
        { path: 'datasetInfoAdmin', component: () => import('@/views/manager/DatasetInfoAdmin.vue')},
        { path: 'algorithmInfo', component: () => import('@/views/manager/AlgorithmInfo.vue')},
        { path: 'algorithmInfoAdmin', component: () => import('@/views/manager/AlgorithmInfoAdmin.vue')},
        { path: 'serverInfo', component: () => import('@/views/manager/ServerInfo.vue')},
        { path: 'upload', component: () => import('@/views/manager/Upload.vue')},
      ]
    },
    { path: '/login', component: () => import('@/views/Login.vue')},
    { path: '/register', component: () => import('@/views/Register.vue')},
  ]
})

router.beforeEach(async (to, from, next) => {
    const token = localStorage.getItem('token')
    if (token) {
        if (to.path === '/login') {
            next('/manager/home')
        } else if (!tokenVerified) {
            // 首次导航时向后端验证 token 是否仍然有效
            try {
                await axios.get(import.meta.env.VITE_BASE_URL + '/verify', {
                    headers: { Authorization: `Bearer ${token}` }
                })
                tokenVerified = true
                next()
            } catch (e) {
                // token 无效（服务器重启/密钥变更/token过期）
                localStorage.removeItem('token')
                localStorage.removeItem('system-user')
                tokenVerified = false
                next('/login')
            }
        } else {
            next()
        }
    } else {
        tokenVerified = false
        if (WHITE_LIST.includes(to.path)) {
            next()
        } else {
            next('/login')
        }
    }
})

export default router
