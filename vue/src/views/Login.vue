<template>
  <div class="login-container">
    <div class="login-bg" aria-hidden="true">
      <div class="grid-layer"></div>
      <div class="glow glow--1"></div>
      <div class="glow glow--2"></div>
      <div class="glow glow--3"></div>
    </div>
    <div class="login-box">
      <div class="login-title">机器人与智能系统实验室</div>
      <div class="login-subtitle">异常检测科研平台</div>
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
      <div v-if="data.registrationEnabled" style="text-align: right; color: #999; font-size: 14px;">
        还没有账号？请 <a href="/register">注册</a>
      </div>
      <div class="login-copyright">
        <div class="version-line">{{ APP_VERSION }}</div>
        Copyright &copy; 2026 机器人与智能系统实验室 All Rights Reserved
      </div>
    </div>
  </div>
</template>

<script setup>
  import { onMounted, reactive, ref } from "vue";
  import { User, Lock } from "@element-plus/icons-vue";
  import request from "@/utils/request";
  import { APP_VERSION } from "@/utils/version";
  import { saveAuthenticatedUser } from "@/utils/auth";
  import {ElMessage} from "element-plus";
  import router from "@/router";

  const data = reactive({
    registrationEnabled: false,
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

  onMounted(async () => {
    try {
      const res = await request.get('/registration-policy')
      data.registrationEnabled = res.code === '200' && res.data?.enabled === true
    } catch {
      data.registrationEnabled = false
    }
  })

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
  position: relative;
  height: 100vh;
  overflow: hidden;
  display: flex;
  justify-content: center;
  align-items: center;
  background: linear-gradient(135deg, #1d2b4a 0%, #2c3e6b 50%, #1a3a5c 100%);
}

.login-bg {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.grid-layer {
  position: absolute;
  inset: -60px;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.035) 1px, transparent 1px);
  background-size: 44px 44px;
  animation: grid-drift 26s linear infinite;
}

.glow {
  position: absolute;
  border-radius: 50%;
  filter: blur(70px);
  opacity: 0.32;
  will-change: transform;
}

.glow--1 {
  width: 420px;
  height: 420px;
  left: -120px;
  top: -140px;
  background: #1a73e8;
  animation: float-a 24s ease-in-out infinite;
}

.glow--2 {
  width: 360px;
  height: 360px;
  right: -100px;
  bottom: -120px;
  background: #00c6ff;
  animation: float-b 30s ease-in-out infinite;
}

.glow--3 {
  width: 260px;
  height: 260px;
  left: 46%;
  top: 58%;
  background: #4f7cff;
  opacity: 0.22;
  animation: float-c 36s ease-in-out infinite;
}

.login-box {
  position: relative;
  z-index: 1;
  width: 420px;
  padding: 40px 36px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25);
}

@keyframes grid-drift {
  from { transform: translate(0, 0); }
  to { transform: translate(44px, 44px); }
}

@keyframes float-a {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(90px, 60px) scale(1.12); }
}

@keyframes float-b {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(-110px, -70px) scale(1.08); }
}

@keyframes float-c {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(70px, -90px) scale(1.15); }
}

@media (prefers-reduced-motion: reduce) {
  .grid-layer,
  .glow {
    animation: none;
  }
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

.login-copyright .version-line {
  margin-bottom: 4px;
  font-weight: 600;
  letter-spacing: 1.5px;
  color: #98a6b8;
}
</style>
