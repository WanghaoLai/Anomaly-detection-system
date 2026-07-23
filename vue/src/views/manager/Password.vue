<template>
  <div class="password-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><Lock /></el-icon></div>
      <div>
        <div class="page-title">修改密码</div>
        <div class="page-subtitle">修改您的账户登录密码</div>
      </div>
    </div>

    <div class="form-card">
      <div class="form-card-header">
        <el-icon class="form-card-icon"><Lock /></el-icon>
        <div>
          <div class="form-card-title">密码修改</div>
          <div class="form-card-desc">为保障账户安全，请设置强度较高的密码</div>
        </div>
      </div>

      <el-form
        ref="formRef"
        :rules="data.rules"
        :model="data.form"
        label-width="100px"
        label-position="left"
        class="styled-form"
      >
        <el-form-item label="原密码" prop="password">
          <el-input v-model="data.form.password" show-password placeholder="请输入当前使用的密码" />
        </el-form-item>
        <el-form-item label="新密码" prop="newPassword">
          <el-input v-model="data.form.newPassword" show-password placeholder="请输入新密码" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirmPasword">
          <el-input v-model="data.form.confirmPasword" show-password placeholder="请再次输入新密码" />
        </el-form-item>

        <div class="form-actions">
          <el-button type="primary" size="large" @click="save">
            <el-icon><Check /></el-icon>确认修改
          </el-button>
        </div>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from "vue"
import { Lock, Check } from "@element-plus/icons-vue"
import request from "@/utils/request"
import { ElMessage } from "element-plus"
import router from "@/router"
import { clearAuthState } from "@/utils/auth"

const formRef = ref()
const data = reactive({
  form: {
    password: '',
    newPassword: '',
    confirmPasword: '',
  },
  rules: {
    password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
    newPassword: [{ required: true, message: '请输入新密码', trigger: 'blur' }],
    confirmPasword: [{ required: true, message: '请确认新密码', trigger: 'blur' }],
  }
})

const save = () => {
  formRef.value.validate(valid => {
    if (valid) {
      if (data.form.password === data.form.newPassword) {
        ElMessage.error('新密码不能和原密码一致')
        return
      }
      if (data.form.newPassword !== data.form.confirmPasword) {
        ElMessage.error('确认新密码错误')
        return
      }
      request.put('/updatePassword', {
        password: data.form.password,
        newPassword: data.form.newPassword,
      }).then(res => {
        if (res.code === '200') {
          ElMessage.success('修改密码成功，请重新登录')
          clearAuthState()
          router.push('/login')
        } else {
          ElMessage.error(res.msg)
        }
      })
    }
  })
}
</script>

<style lang="scss" scoped>
.password-page {
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

.form-card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 24px;
  background: linear-gradient(135deg, #f5f7fa 0%, #eaf3ff 100%);
  border-bottom: 1px solid #eef0f4;
}

.form-card-icon {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #1a73e8;
  color: #ffffff;
  font-size: 18px;
}

.form-card-title {
  font-size: 15px;
  font-weight: 600;
  color: #263247;
  line-height: 20px;
}

.form-card-desc {
  font-size: 12px;
  color: #9097a5;
  margin-top: 2px;
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
