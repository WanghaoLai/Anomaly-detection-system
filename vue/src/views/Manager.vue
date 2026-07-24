<template>
  <div>
    <div class="header">
      <div style="flex: 1">
        <div style="padding-left: 20px; display: flex; align-items: center">
          <img src="@/assets/imgs/logo.jpg" alt="" style="width: 38px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.3)">
          <div class="header-title">机器人与智能系统实验室 · 异常检测系统</div>
        </div>
      </div>
      <div class="time-display">
        <el-icon><Clock /></el-icon>
        <span class="time-text">{{ currentTime }}</span>
      </div>
      <div class="user-panel">
        <img class="user-avatar" :src="data.user.avatar || 'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png'" alt="">
        <div style="display: flex; flex-direction: column; margin-left: 10px">
          <span class="user-name">{{ data.user.name }}</span>
          <span class="user-role">{{ data.user.role }}</span>
        </div>
      </div>
    </div>

    <div style="display: flex">
      <div class="sidebar">
        <el-menu
            router
            class="sidebar-menu"
            :default-active="router.currentRoute.value.fullPath"
            :default-openeds="['user']"
        >
          <el-menu-item index="/manager/home">
            <el-icon><HomeFilled /></el-icon>
            <span>系统首页</span>
          </el-menu-item>
          <el-sub-menu index="user" v-if="data.user.role === '管理员'">
            <template #title>
              <el-icon><Avatar /></el-icon>
              <span>用户管理</span>
            </template>
            <el-menu-item index="/manager/admin">
              <el-icon><Key /></el-icon>
              <span>管理员信息</span>
            </el-menu-item>
            <el-menu-item index="/manager/user">
              <el-icon><User /></el-icon>
              <span>用户信息</span>
            </el-menu-item>
          </el-sub-menu>
          <el-sub-menu index="dataset" v-if="data.user.role === '管理员'">
            <template #title>
              <el-icon><FolderOpened /></el-icon>
              <span>数据集</span>
            </template>
            <el-menu-item index="/manager/datasetAdmin">
              <el-icon><UploadFilled /></el-icon>
              <span>数据集管理</span>
            </el-menu-item>
            <el-menu-item index="/manager/datasetInfoAdmin">
              <el-icon><Coin /></el-icon>
              <span>数据集信息</span>
            </el-menu-item>
          </el-sub-menu>
          <el-sub-menu index="algorithm" v-if="data.user.role === '管理员'">
            <template #title>
              <el-icon><SetUp /></el-icon>
              <span>算法</span>
            </template>
            <el-menu-item index="/manager/algorithmAdmin">
              <el-icon><SetUp /></el-icon>
              <span>算法管理</span>
            </el-menu-item>
            <el-menu-item index="/manager/algorithmInfoAdmin">
              <el-icon><Cpu /></el-icon>
              <span>算法信息</span>
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item index="/manager/datasetInfo" v-if="data.user.role === '用户'">
            <el-icon><Coin /></el-icon>
            <span>数据集信息</span>
          </el-menu-item>
          <el-menu-item index="/manager/algorithmInfo" v-if="data.user.role === '用户'">
            <el-icon><Cpu /></el-icon>
            <span>算法信息</span>
          </el-menu-item>
          <el-menu-item index="/manager/serverInfo">
            <el-icon><Monitor /></el-icon>
            <span>服务器信息</span>
          </el-menu-item>
          <el-menu-item index="/manager/upload" v-if="data.user.role === '用户'">
            <el-icon><DataLine /></el-icon>
            <span>算法训练</span>
          </el-menu-item>
          <el-menu-item index="/manager/upload" v-if="data.user.role === '用户'">
            <el-icon><TrendCharts /></el-icon>
            <span>算法推理</span>
          </el-menu-item>
          <el-menu-item index="/manager/upload" v-if="data.user.role === '用户'">
            <el-icon><Histogram /></el-icon>
            <span>实验结果可视化</span>
          </el-menu-item>
          <el-menu-item index="/manager/notice" v-if="data.user.role === '管理员'">
            <el-icon><Bell /></el-icon>
            <span>公告管理</span>
          </el-menu-item>
          <el-menu-item index="/manager/knowledge" v-if="data.user.role === '管理员'">
            <el-icon><Collection /></el-icon>
            <span>知识库管理</span>
          </el-menu-item>
          <el-menu-item index="/manager/chat" v-if="data.user.role === '用户'">
            <el-icon><ChatDotRound /></el-icon>
            <span>智能助手</span>
          </el-menu-item>
          <el-menu-item index="/manager/person">
            <el-icon><User /></el-icon>
            <span>个人资料</span>
          </el-menu-item>
          <el-menu-item index="/manager/password">
            <el-icon><Lock /></el-icon>
            <span>修改密码</span>
          </el-menu-item>
          <el-menu-item index="/login" @click="logout">
            <el-icon><SwitchButton /></el-icon>
            <span>退出系统</span>
          </el-menu-item>
        </el-menu>
        <div class="sidebar-copyright">
          <div>Copyright &copy; 2026</div>
          <div>机器人与智能系统实验室</div>
          <div>All Rights Reserved</div>
        </div>
      </div>

      <div class="manager-main">
        <router-view @updateUser="updateUser" />
      </div>
    </div>

  </div>
</template>

<script setup>
import { reactive, ref, onMounted, onUnmounted } from "vue";
import router from "@/router";
import {ElMessage} from "element-plus";
import request from "@/utils/request";
import { clearAuthState } from "@/utils/auth";

const currentTime = ref('')
let timer = null

const updateTime = () => {
  const now = new Date()
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  const week = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()]
  const h = String(now.getHours()).padStart(2, '0')
  const min = String(now.getMinutes()).padStart(2, '0')
  const s = String(now.getSeconds()).padStart(2, '0')
  currentTime.value = `${y}-${m}-${d} 星期${week} ${h}:${min}:${s}`
}

onMounted(() => {
  updateTime()
  timer = setInterval(updateTime, 1000)
})

onUnmounted(() => {
  clearInterval(timer)
})

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}')
})

if (!data.user?.id) {
  ElMessage.error('请登录！')
  router.push('/login')
}

const updateUser = () => {
  data.user = JSON.parse(localStorage.getItem('system-user') || '{}')
}

const logout = async () => {
  // 先标记为主动退出，避免尚未完成的接口请求把 401 误报为登录过期。
  clearAuthState()
  try {
    await request.post('/logout')
  } finally {
    ElMessage.success('退出成功')
    router.push('/login')
  }
}
</script>

<style lang="scss" scoped>
.header {
  height: 60px;
  display: flex;
  align-items: center;
  background: linear-gradient(90deg, #1d2b4a 0%, #2c3e6b 100%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.header-title {
  font-size: 25px;
  font-weight: 700;
  margin-left: 12px;
  background: linear-gradient(90deg, #ffffff 0%, #90caf9 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: 0;
}

.time-display {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  margin-right: 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  color: #90caf9;
  font-size: 16px;
  font-family: 'Courier New', monospace;
  letter-spacing: 0.5px;
}

.user-panel {
  display: flex;
  align-items: center;
  padding: 6px 16px;
  margin-right: 12px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  cursor: default;
  transition: background 0.2s ease;
}

.user-panel:hover {
  background: rgba(255, 255, 255, 0.12);
}

.user-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.25);
  object-fit: cover;
}

.user-name {
  color: #ffffff;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.2;
}

.user-role {
  color: #90caf9;
  font-size: 11px;
  margin-top: 2px;
}

.sidebar {
  width: 210px;
  min-height: calc(100vh - 60px);
  background: linear-gradient(180deg, #1d2b4a 0%, #2c3e6b 100%);
  display: flex;
  flex-direction: column;
}

.sidebar-copyright {
  margin-top: auto;
  padding: 16px 12px;
  text-align: center;
  font-size: 11px;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.25);
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.manager-main {
  flex: 1;
  width: 0;
  padding: 10px;
  background-color: #f0f2f5;
}

@media (max-width: 720px) {
  .header-title {
    max-width: 210px;
    font-size: 15px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .user-panel {
    padding: 4px 6px;
    margin-right: 6px;
  }

  .user-panel > div {
    display: none !important;
  }

  .user-avatar {
    width: 30px;
    height: 30px;
  }

  .sidebar {
    width: 64px;
  }

  .manager-main {
    padding: 6px;
  }
}
</style>

<style lang="scss">
.sidebar-menu {
  border: none;
  background: transparent;
}

.sidebar-menu .el-menu-item,
.sidebar-menu .el-sub-menu__title {
  height: 44px;
  line-height: 44px;
  margin: 4px 8px;
  border-radius: 8px;
  color: #a0aec0;
  font-size: 13px;
  transition: all 0.2s ease;
}

.sidebar-menu .el-menu-item:hover,
.sidebar-menu .el-sub-menu__title:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
}

.sidebar-menu .el-menu-item.is-active {
  background: linear-gradient(90deg, #1a73e8, #00c6ff);
  color: #ffffff;
  font-weight: 600;
}

.sidebar-menu .el-sub-menu .el-menu-item {
  padding-left: 52px !important;
}

.sidebar-menu .el-sub-menu .el-menu {
  background: transparent;
}

.sidebar th {
  color: #333;
}

@media (max-width: 720px) {
  .sidebar-menu .el-menu-item,
  .sidebar-menu .el-sub-menu__title {
    justify-content: center;
    margin: 4px 8px;
    padding: 0 !important;
  }

  .sidebar-menu .el-menu-item span,
  .sidebar-menu .el-sub-menu__title span,
  .sidebar-menu .el-sub-menu__icon-arrow {
    display: none;
  }

  .sidebar-menu .el-menu-item .el-icon,
  .sidebar-menu .el-sub-menu__title .el-icon {
    margin-right: 0;
  }

  .sidebar-menu .el-sub-menu .el-menu-item {
    padding-left: 0 !important;
  }
}
</style>
