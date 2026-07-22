<template>
  <div class="person-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><User /></el-icon></div>
      <div>
        <div class="page-title">个人资料</div>
        <div class="page-subtitle">管理您的账户基本信息</div>
      </div>
    </div>

    <div class="form-card">
      <div class="avatar-section">
        <div class="avatar-wrapper">
          <el-upload
            :show-file-list="false"
            class="avatar-uploader"
            :action="uploadUrl"
            :on-success="handleFileUpload"
            :headers="uploadHeaders"
          >
            <img v-if="data.user.avatar" :src="data.user.avatar" class="avatar-img" />
            <div v-else class="avatar-placeholder">
              <el-icon><Plus /></el-icon>
            </div>
            <div class="avatar-overlay">
              <el-icon><Camera /></el-icon>
              <span>更换头像</span>
            </div>
          </el-upload>
        </div>
        <div class="avatar-info">
          <div class="avatar-name">{{ data.user.name || '未设置名称' }}</div>
          <el-tag :type="data.user.role === '管理员' ? '' : 'success'" effect="light" round size="small">
            <el-icon style="margin-right: 3px"><Key v-if="data.user.role === '管理员'" /><User v-else /></el-icon>
            {{ data.user.role }}
          </el-tag>
        </div>
      </div>

      <el-form
        ref="formRef"
        :model="data.user"
        :rules="data.rules"
        label-width="100px"
        label-position="left"
        class="styled-form"
      >
        <el-form-item label="账号" prop="username">
          <el-input disabled v-model="data.user.username" autocomplete="off">
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="data.user.name" autocomplete="off" placeholder="请输入您的名称">
            <template #prefix><el-icon><Edit /></el-icon></template>
          </el-input>
        </el-form-item>

        <div class="form-actions">
          <el-button type="primary" size="large" @click="save">
            <el-icon><Check /></el-icon>保存修改
          </el-button>
        </div>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from "vue"
import { User, Plus, Camera, Edit, Check, Key } from "@element-plus/icons-vue"
import request from "@/utils/request"
import { ElMessage } from "element-plus"

const uploadUrl = import.meta.env.VITE_BASE_URL + '/files/upload'
const uploadHeaders = { Authorization: `Bearer ${localStorage.getItem('token')}` }

const formRef = ref()
const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  rules: {
    username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
    name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  }
})

const handleFileUpload = (file) => {
  data.user.avatar = file.data
}

const emit = defineEmits(["updateUser"])

const save = () => {
  formRef.value.validate(valid => {
    if (valid) {
      const api = data.user.role === '管理员' ? '/admin/update' : '/user/update'
      request.put(api, data.user).then(res => {
        if (res.code === '200') {
          ElMessage.success('更新成功')
          localStorage.setItem('system-user', JSON.stringify(data.user))
          emit('updateUser')
        } else {
          ElMessage.error(res.msg)
        }
      })
    }
  })
}
</script>

<style lang="scss" scoped>
.person-page {
  height: calc(100vh - 80px);
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}

.page-header {
  min-height: 54px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: #ffffff;
  border-left: 4px solid #1a73e8;
  border-radius: 5px;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.06);
}

.page-icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 5px;
  background: #eaf3ff;
  color: #1a73e8;
  font-size: 20px;
}

.page-title {
  font-size: 17px;
  line-height: 22px;
  font-weight: 700;
  color: #263247;
}

.page-subtitle {
  font-size: 12px;
  line-height: 16px;
  color: #9097a5;
  margin-top: 2px;
}

.form-card {
  flex: 1;
  background: #ffffff;
  border-radius: 5px;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.06);
  overflow: hidden;
}

.avatar-section {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 24px 32px;
  background: linear-gradient(135deg, #f5f7fa 0%, #eaf3ff 100%);
  border-bottom: 1px solid #eef0f4;
}

.avatar-wrapper {
  position: relative;
  flex-shrink: 0;
}

.avatar-uploader {
  cursor: pointer;
}

.avatar-uploader :deep(.el-upload) {
  border-radius: 50%;
  overflow: hidden;
  position: relative;
  display: block;
}

.avatar-img {
  width: 80px;
  height: 80px;
  display: block;
  border-radius: 50%;
  border: 3px solid #ffffff;
  box-shadow: 0 2px 12px rgba(26, 115, 232, 0.2);
  object-fit: cover;
}

.avatar-placeholder {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: #eaf3ff;
  color: #1a73e8;
  font-size: 28px;
  border: 3px solid #ffffff;
  box-shadow: 0 2px 12px rgba(26, 115, 232, 0.15);
}

.avatar-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 80px;
  height: 80px;
  border-radius: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  background: rgba(0, 0, 0, 0.45);
  color: #ffffff;
  opacity: 0;
  transition: opacity 0.25s ease;
  font-size: 11px;
}

.avatar-overlay .el-icon {
  font-size: 18px;
}

.avatar-wrapper:hover .avatar-overlay {
  opacity: 1;
}

.avatar-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.avatar-name {
  font-size: 18px;
  font-weight: 700;
  color: #263247;
  line-height: 22px;
}

.styled-form {
  padding: 28px 32px 20px;
  max-width: 560px;
}

.styled-form :deep(.el-form-item) {
  margin-bottom: 22px;
}

.styled-form :deep(.el-form-item__label) {
  color: #38455b;
  font-weight: 500;
}

.styled-form :deep(.el-input__wrapper) {
  border-radius: 6px;
}

.styled-form :deep(.el-input.is-disabled .el-input__wrapper) {
  background-color: #f5f7fa;
}

.form-actions {
  text-align: center;
  padding-top: 12px;
  border-top: 1px solid #f0f2f5;
  margin-top: 8px;
}

.form-actions .el-button {
  min-width: 160px;
  border-radius: 6px;
}
</style>
