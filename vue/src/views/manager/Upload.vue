<template>
  <div style="display: flex; justify-content: center; align-items: center; min-height: calc(100vh - 120px);">
    <div style="text-align: center;">
      <div style="font-size: 80px; margin-bottom: 20px;">🚧</div>
      <div style="font-size: 28px; font-weight: bold; color: #333; margin-bottom: 12px;">页面正在开发中...</div>
      <div style="font-size: 16px; color: #999; margin-bottom: 30px;">工程师正在努力敲代码，请稍后再来～</div>
      <el-progress :percentage="68" :stroke-width="14" style="width: 400px; margin: 0 auto;" />
      <div style="margin-top: 30px;">
        <el-button type="primary" plain @click="$router.push('/manager/home')">返回首页</el-button>
      </div>
    </div>
  </div>
</template>
