<template>
  <div>
    <div class="header">
      <div style="flex: 1">
        <div style="padding-left: 20px; display: flex; align-items: center">
          <img src="@/assets/imgs/logo.jpg" alt="" style="width: 38px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.3)">
          <div class="header-title">机器人与智能系统实验室 · 异常检测系统</div>
        </div>
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
              <span>数据集管理</span>
            </template>
            <el-menu-item index="/manager/upload">
              <el-icon><UploadFilled /></el-icon>
              <span>上传数据集</span>
            </el-menu-item>
            <el-menu-item index="/manager/datasetInfoAdmin">
              <el-icon><Coin /></el-icon>
              <span>数据集信息</span>
            </el-menu-item>
          </el-sub-menu>
          <el-sub-menu index="algorithm" v-if="data.user.role === '管理员'">
            <template #title>
              <el-icon><SetUp /></el-icon>
              <span>算法管理</span>
            </template>
            <el-menu-item index="/manager/upload">
              <el-icon><Upload /></el-icon>
              <span>上传算法</span>
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
          <el-menu-item index="/manager/notice" v-if="data.user.role === '管理员'">
            <el-icon><Bell /></el-icon>
            <span>公告管理</span>
          </el-menu-item>
          <el-menu-item index="/manager/knowledge" v-if="data.user.role === '管理员'">
            <el-icon><Collection /></el-icon>
            <span>知识库管理</span>
          </el-menu-item>
          <el-menu-item index="/manager/chat">
            <el-icon><ChatDotRound /></el-icon>
            <span>智能客服</span>
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
      </div>

      <div style="flex: 1; width: 0; background-color: #f0f2f5; padding: 10px">
        <router-view @updateUser="updateUser" />
      </div>
    </div>

  </div>
</template>

<script setup>
import { reactive } from "vue";
import router from "@/router";
import {ElMessage} from "element-plus";

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

const logout = () => {
  ElMessage.success('退出成功')
  localStorage.removeItem('token')
  localStorage.removeItem('system-user')
  router.push('/login')
}
</script>

<style scoped>
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
  letter-spacing: 2px;
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
}

:deep(.sidebar-menu) {
  border: none;
  background: transparent;
}

:deep(.sidebar-menu .el-menu-item),
:deep(.sidebar-menu .el-sub-menu__title) {
  height: 44px;
  line-height: 44px;
  margin: 4px 8px;
  border-radius: 8px;
  color: #a0aec0;
  font-size: 13px;
  transition: all 0.2s ease;
}

:deep(.sidebar-menu .el-menu-item:hover),
:deep(.sidebar-menu .el-sub-menu__title:hover) {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
}

:deep(.sidebar-menu .el-menu-item.is-active) {
  background: linear-gradient(90deg, #1a73e8, #00c6ff);
  color: #ffffff;
  font-weight: 600;
}

:deep(.sidebar-menu .el-sub-menu .el-menu-item) {
  padding-left: 52px !important;
}

:deep(.sidebar-menu .el-sub-menu .el-menu) {
  background: transparent;
}

:deep(th) {
  color: #333;
}
</style>
