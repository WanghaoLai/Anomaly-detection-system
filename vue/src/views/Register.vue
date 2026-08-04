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
        <el-form-item prop="confirmPassword">
          <el-input :prefix-icon="Lock" size="large" v-model="data.form.confirmPassword" placeholder="请确认密码" show-password />
        </el-form-item>
        <el-form-item>
          <el-button size="large" type="primary" class="login-btn" @click="register">注 册</el-button>
        </el-form-item>
      </el-form>
      <div style="text-align: right; color: #999; font-size: 14px;">
        已有账号？请 <a href="/login">登录</a>
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
  import {ElMessage} from "element-plus";
  import router from "@/router";

  const validatePass = (rule, value, callback) => {
    if (!value) {
      callback(new Error('请确认密码'))
    } else if (value !== data.form.password) {
      callback(new Error('两次输入密码不一致'))
    } else {
      callback()
    }
  }

  const data = reactive({
    form: { role: 'USER' },
    rules: {
      username: [
        { required: true, message: '请输入账号', trigger: 'blur' },
      ],
      password: [
        { required: true, message: '请输入密码', trigger: 'blur' },
      ],
      confirmPassword: [
        { validator: validatePass, trigger: 'blur' },
      ],
    }
  })


  const formRef = ref()

  // 点击注册按钮的时候会触发这个方法
  const register = () => {
    formRef.value.validate((valid => {
      if (valid) {
        // 调用后台的接口
        request.post('/register', data.form).then(res => {
          if (res.code === '200') {
            ElMessage.success("注册成功")
            router.push('/login')
          } else {
            ElMessage.error(res.msg)
          }
        }).catch(error => {
          ElMessage.error(error.response?.data?.msg || '注册失败，请稍后重试')
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