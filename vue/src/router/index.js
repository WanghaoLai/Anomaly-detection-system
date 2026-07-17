import {createRouter, createWebHistory} from 'vue-router'

const WHITE_LIST = ['/login', '/register']

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
        { path: 'upload', component: () => import('@/views/manager/Upload.vue')},
      ]
    },
    { path: '/login', component: () => import('@/views/Login.vue')},
    { path: '/register', component: () => import('@/views/Register.vue')},
  ]
})

router.beforeEach((to, from, next) => {
    const token = localStorage.getItem('token')
    if (token) {
        if (to.path === '/login') {
            next('/manager/home')
        } else {
            next()
        }
    } else {
        if (WHITE_LIST.includes(to.path)) {
            next()
        } else {
            next('/login')
        }
    }
})

export default router
