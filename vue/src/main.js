import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { APP_VERSION } from './utils/version'

import '@/assets/css/global.css'

document.title = `工业异常检测科研平台 ${APP_VERSION}`

const app = createApp(App)

app.use(router)
app.mount('#app')
