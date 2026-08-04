<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-title">机器人与智能系统实验室</div>
      <div class="login-subtitle">异常检测系统</div>
      <el-form :model="data.form"  ref="formRef" :rules="data.rules">
        <el-form-item prop="username">
          <el-input :prefix-icon="User" size="large" v-model="data.form.username" placeholder="请输入账号" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input :prefix-icon="Lock" size="large" v-model="data.form.password" placeholder="请输入密码" show-password />
        </el-form-item>
        <el-form-item prop="role">
          <el-select size="large" style="width: 100%" v-model="data.form.role">
            <el-option value="用户" label="用户"></el-option>
            <el-option value="管理员" label="管理员"></el-option>
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button size="large" type="primary" class="login-btn" @click="login">登 录</el-button>
        </el-form-item>
      </el-form>
      <div style="text-align: right; color: #999; font-size: 14px;">
        还没有账号？请 <a href="/register">注册</a>
      </div>
      <div class="login-copyright">
        Copyright &copy; 2026 机器人与智能系统实验室 All Rights Reserved
      </div>
    </div>
  </div>
</template>

<script setup>
  import { reactive, ref } from "vue";
  import { User, Lock } from "@element-plus/icons-vue";
  import request from "@/utils/request";
  import { saveAuthenticatedUser } from "@/utils/auth";
  import {ElMessage} from "element-plus";
  import router from "@/router";

  const data = reactive({
    // 普通用户是系统的默认登录主体；管理员必须显式选择管理员身份。
    form: { role: '用户' },
    rules: {
      username: [
        { required: true, message: '请输入账号', trigger: 'blur' },
      ],
      password: [
        { required: true, message: '请输入密码', trigger: 'blur' },
      ],
      role: [
        { required: true, message: '请选择登录身份', trigger: 'change' },
      ],
    }
  })

  const formRef = ref()

  // 点击登录按钮的时候会触发这个方法
  const login = () => {
    formRef.value.validate((valid => {
      if (valid) {
        // 调用后台的接口
        request.post('/login', data.form).then(res => {
          if (res.code === '200') {
            ElMessage.success("登录成功")
            saveAuthenticatedUser(res)
            router.push('/manager/home')
          } else {
            ElMessage.error(res.msg)
          }
        }).catch(error => {
          ElMessage.error(error.response?.data?.msg || '登录失败，请稍后重试')
        })
      }
    })).catch(error => {
      console.error(error)
    })
  }

</script>

<style scoped>
.login-container {
  height: 100vh;
  overflow: hidden;
  display: flex;
  justify-content: center;
  align-items: center;
  background: linear-gradient(135deg, #1d2b4a 0%, #2c3e6b 50%, #1a3a5c 100%);
}

.login-box {
  width: 420px;
  padding: 40px 36px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25);
}

.login-title {
  font-size: 22px;
  font-weight: 700;
  text-align: center;
  color: #1d2b4a;
  letter-spacing: 2px;
}

.login-subtitle {
  font-size: 16px;
  text-align: center;
  color: #1a73e8;
  margin-bottom: 30px;
  margin-top: 6px;
  letter-spacing: 4px;
}

.login-btn {
  width: 100%;
  background: linear-gradient(90deg, #1a73e8, #00c6ff);
  border: none;
  letter-spacing: 4px;
}

a {
  color: #1a73e8;
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

.login-copyright {
  margin-top: 24px;
  text-align: center;
  font-size: 12px;
  color: #b0b0b0;
  letter-spacing: 0.5px;
}
</style>
