<template>
  <div>
    <div class="welcome-banner">
      <div class="welcome-left">
        <div class="welcome-text">欢迎您，{{ data.user.name }}</div>
        <div class="welcome-sub">欢迎使用机器人与智能系统实验室 · 异常检测系统，祝您今天工作顺利！</div>
      </div>
    </div>

    <div class="card notice-card">
      <div class="notice-header">
        <el-icon><Bell /></el-icon>
        <span>系统公告</span>
      </div>
      <div v-if="data.noticeData && data.noticeData.length">
        <el-timeline>
          <el-timeline-item
            v-for="item in data.noticeData"
            :key="item.id"
            :timestamp="item.time"
            placement="top"
            color="#1a73e8"
          >
            <div class="notice-item">
              <h4 class="notice-title">{{ item.name }}</h4>
              <p class="notice-content">{{ item.content }}</p>
            </div>
          </el-timeline-item>
        </el-timeline>
      </div>
      <el-empty v-else description="暂无公告" :image-size="100" />
    </div>
  </div>
</template>

<script setup>
import { reactive } from "vue";
import request from "@/utils/request";
import { ElMessage } from "element-plus";

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  noticeData: [],
})

const loadNotice = () => {
  request.get('/notice/selectAll').then(res => {
    if (res.code === '200') {
      data.noticeData = res.data
    } else {
      ElMessage.error(res.msg)
    }
  })
}
loadNotice()
</script>

<style scoped>
.welcome-banner {
  background: linear-gradient(135deg, #1d2b4a 0%, #2c3e6b 100%);
  border-radius: 12px;
  padding: 28px 32px;
  margin-bottom: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.welcome-text {
  color: #ffffff;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 1px;
}

.welcome-sub {
  color: #90caf9;
  font-size: 14px;
  margin-top: 8px;
}

.notice-card {
  background: #ffffff;
  border-radius: 12px;
  padding: 24px 28px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}

.notice-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 17px;
  font-weight: 700;
  color: #1d2b4a;
  margin-bottom: 20px;
  padding-bottom: 14px;
  border-bottom: 2px solid #f0f2f5;
}

.notice-header .el-icon {
  color: #1a73e8;
}

.notice-item {
  background: #f8faff;
  border-radius: 8px;
  padding: 14px 18px;
  border-left: 3px solid #1a73e8;
}

.notice-title {
  color: #1d2b4a;
  font-size: 15px;
  margin: 0 0 6px;
}

.notice-content {
  color: #666;
  font-size: 13px;
  margin: 0;
  line-height: 1.6;
}
</style>
