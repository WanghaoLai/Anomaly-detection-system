<template>
  <div class="inference-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><DataAnalysis /></el-icon></div>
      <div class="page-info">
        <div class="page-title">算法推理</div>
        <div class="page-subtitle">选择已完成训练的模型，提交推理任务并跟踪执行状态</div>
      </div>
    </div>

    <div class="inference-card">
      <div class="card-header">
        <div class="card-title">
          <el-icon><Promotion /></el-icon>
          <span>提交推理任务</span>
        </div>
        <span class="card-hint">并发上限 {{ state.options.maxConcurrentJobs || '--' }} 个任务</span>
      </div>
      <div class="form-area">
        <el-form :model="form" label-width="100px">
          <el-form-item label="来源模型" required>
            <el-select v-model="form.trainingJobId" filterable style="width: 100%" @change="modelChanged">
              <el-option
                v-for="item in state.options.models"
                :key="item.id"
                :label="`${item.algorithmName} · ${item.datasetName} · ${shortNo(item.jobNo)}`"
                :value="item.id"
              />
            </el-select>
            <div v-if="selectedModel" class="model-summary">
              <div class="summary-item">
                <span>当前算法</span>
                <strong>{{ selectedModel.algorithmName }}</strong>
              </div>
              <div class="summary-item">
                <span>数据集</span>
                <strong>{{ selectedModel.datasetName }}</strong>
              </div>
              <div class="summary-item">
                <span>训练任务</span>
                <strong>{{ shortNo(selectedModel.jobNo) }}</strong>
              </div>
            </div>
          </el-form-item>
          <el-form-item label="目标类别">
            <el-select v-model="form.classes" multiple collapse-tags collapse-tags-tooltip style="width: 100%">
              <el-option v-for="name in selectedModel?.classes || []" :key="name" :label="name" :value="name" />
            </el-select>
            <div class="hint">不选择时默认评估该任务训练过的全部类别</div>
          </el-form-item>
          <el-form-item label="GPU">
            <el-select v-model="form.requestedGpu" clearable placeholder="自动选择" style="width: 220px">
              <el-option v-for="gpu in state.options.gpuOptions" :key="gpu" :label="`GPU ${gpu}`" :value="gpu" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Promotion" :loading="state.submitting" @click="submit">开始推理</el-button>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <div class="inference-card">
      <div class="card-header">
        <div class="card-title">
          <el-icon><Tickets /></el-icon>
          <span>推理记录</span>
        </div>
        <el-tooltip content="刷新推理记录" placement="top">
          <el-button :icon="Refresh" circle :loading="state.loading" @click="loadJobs" />
        </el-tooltip>
      </div>
      <div class="table-area">
        <el-table :data="state.jobs" v-loading="state.loading" stripe empty-text="暂无推理任务">
          <el-table-column label="任务编号" min-width="130">
            <template #default="{ row }"><span class="job-no">{{ shortNo(row.jobNo) }}</span></template>
          </el-table-column>
          <el-table-column prop="algorithmName" label="算法" min-width="160" show-overflow-tooltip />
          <el-table-column prop="datasetName" label="数据集" min-width="120" show-overflow-tooltip />
          <el-table-column label="评估类别" min-width="180">
            <template #default="{ row }">{{ row.config?.parameters?.classes?.join(', ') || '全部类别' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" effect="light" round>{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="GPU" width="80" align="center">
            <template #default="{ row }">{{ row.assignedGpu ?? '--' }}</template>
          </el-table-column>
          <el-table-column label="提交时间" min-width="160">
            <template #default="{ row }">{{ dateTime(row.submittedAt) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="160" align="center" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="showDetail(row)">详情</el-button>
              <el-button v-if="row.status === 'SUCCEEDED'" link type="success" @click="openResults">实验结果</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div class="pagination-bar">
        <el-pagination
          background
          layout="total, prev, pager, next"
          :total="state.total"
          :page-size="state.pageSize"
          v-model:current-page="state.pageNum"
          @current-change="loadJobs"
        />
      </div>
    </div>

    <el-dialog v-model="state.detailVisible" title="推理任务详情" width="760px" align="center">
      <el-alert
        v-if="state.detail?.failureReason"
        class="detail-alert"
        type="error"
        :closable="false"
        :title="state.detail.failureReason"
        show-icon
      />
      <el-descriptions v-if="state.detail" :column="2" border>
        <el-descriptions-item label="算法">{{ state.detail.algorithmName || '--' }}</el-descriptions-item>
        <el-descriptions-item label="数据集">{{ state.detail.datasetName || '--' }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="statusType(state.detail.status)" effect="light" round>{{ statusLabel(state.detail.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="GPU">{{ state.detail.assignedGpu ?? '--' }}</el-descriptions-item>
        <el-descriptions-item label="推理任务">{{ state.detail.jobNo }}</el-descriptions-item>
        <el-descriptions-item label="来源训练任务">{{ state.detail.trainingJobNo || `#${state.detail.trainingJobId}` }}</el-descriptions-item>
        <el-descriptions-item label="目标类别" :span="2">{{ state.detail.config?.parameters?.classes?.join(', ') || '--' }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ dateTime(state.detail.submittedAt) }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ dateTime(state.detail.startedAt) }}</el-descriptions-item>
        <el-descriptions-item label="完成时间">{{ dateTime(state.detail.finishedAt) }}</el-descriptions-item>
        <el-descriptions-item label="运行耗时">{{ duration(state.detail.startedAt, state.detail.finishedAt) }}</el-descriptions-item>
        <el-descriptions-item label="退出码">{{ state.detail.exitCode ?? '--' }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="state.detail?.status === 'SUCCEEDED'" class="result-guide">
        <div class="result-text">
          <strong>推理已完成</strong>
          <span>图片预览、对比和下载已归档到实验结果可视化页面</span>
        </div>
        <el-button type="success" plain :icon="Picture" @click="openResults">查看实验结果</el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive } from 'vue'
import { DataAnalysis, Picture, Promotion, Refresh, Tickets } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'
import router from '@/router'

const state = reactive({
  options: { models: [], gpuOptions: [] }, jobs: [], total: 0, pageNum: 1, pageSize: 10,
  loading: false, submitting: false, detailVisible: false, detail: null,
})
const form = reactive({ trainingJobId: null, classes: [], requestedGpu: null })
let timer = null

const selectedModel = computed(() => state.options.models.find(item => item.id === form.trainingJobId))
const shortNo = value => value ? value.slice(0, 8) : '--'
const dateTime = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '--'
const duration = (startedAt, finishedAt) => {
  if (!startedAt || !finishedAt) return '--'
  const seconds = Math.max(0, Math.round((new Date(finishedAt) - new Date(startedAt)) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} 分 ${seconds % 60} 秒`
}
const statusLabel = value => ({ QUEUED: '排队中', STARTING: '启动中', RUNNING: '推理中', SUCCEEDED: '成功', FAILED: '失败', STOPPED: '已停止', LOST: '失联' }[value] || value)
const statusType = value => ({ SUCCEEDED: 'success', FAILED: 'danger', LOST: 'danger', RUNNING: 'primary', STARTING: 'warning', QUEUED: 'info' }[value] || 'info')
const modelChanged = () => { form.classes = [] }
const openResults = () => router.push({ path: '/manager/experimentResults', query: { sourceType: 'INFERENCE' } })

const loadOptions = async () => {
  const res = await request.get('/inference/options')
  state.options = res.data
}
const loadJobs = async () => {
  state.loading = true
  try {
    const res = await request.get('/inference/jobs', { params: { pageNum: state.pageNum, pageSize: state.pageSize } })
    state.jobs = res.data.list
    state.total = res.data.total
  } finally { state.loading = false }
}
const submit = async () => {
  if (!form.trainingJobId) return ElMessage.warning('请选择成功训练模型')
  state.submitting = true
  try {
    await request.post('/inference/jobs', {
      trainingJobId: form.trainingJobId, classes: form.classes, requestedGpu: form.requestedGpu,
    })
    ElMessage.success('推理任务已提交')
    await loadJobs()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '推理任务提交失败')
  } finally { state.submitting = false }
}
const showDetail = async row => {
  const res = await request.get(`/inference/jobs/${row.id}`)
  state.detail = res.data
  state.detailVisible = true
  await loadJobs()
}

onMounted(async () => {
  await Promise.all([loadOptions(), loadJobs()])
  timer = setInterval(loadJobs, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style lang="scss" scoped>
.inference-page {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: calc(100vh - 80px);
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
  flex: 0 0 38px;
  display: grid;
  place-items: center;
  border-radius: 5px;
  background: #eaf3ff;
  color: #1a73e8;
  font-size: 20px;
}

.page-info {
  flex: 1;
  min-width: 0;
}

.page-title {
  font-size: 17px;
  line-height: 22px;
  font-weight: 700;
  color: #263247;
}

.page-subtitle {
  margin-top: 2px;
  color: #9097a5;
  font-size: 12px;
  line-height: 16px;
}

.inference-card {
  background: #ffffff;
  border-radius: 5px;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.06);
  overflow: hidden;
}

.card-header {
  min-height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid #f0f2f5;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #29364b;
  font-size: 14px;
  font-weight: 600;
}

.card-title .el-icon {
  color: #1a73e8;
  font-size: 16px;
}

.card-hint {
  color: #9097a5;
  font-size: 12px;
}

.form-area {
  padding: 16px 16px 0;
}

.form-area :deep(.el-form-item__label) {
  color: #44516a;
  font-size: 13px;
}

.model-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 10px;
  padding: 12px 16px;
  border: 1px solid #e1ecf7;
  border-radius: 5px;
  background: #f5f9ff;
}

.summary-item span,
.summary-item strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.summary-item span {
  margin-bottom: 4px;
  color: #8a96a5;
  font-size: 12px;
}

.summary-item strong {
  color: #27364a;
  font-size: 13px;
  font-weight: 600;
}

.hint {
  margin-top: 4px;
  color: #9097a5;
  font-size: 12px;
  line-height: 1.5;
}

.table-area {
  padding: 0 16px;
}

.table-area :deep(.el-table) {
  --el-table-border-color: #eef0f4;
  --el-table-header-bg-color: #f5f7fa;
  --el-table-row-hover-bg-color: #f0f5ff;
  font-size: 13px;
  border-radius: 0;
}

.table-area :deep(.el-table th.el-table__cell) {
  padding: 6px 0;
  font-size: 12px;
  font-weight: 600;
  color: #38455b;
  background-color: #f5f7fa;
}

.table-area :deep(.el-table td.el-table__cell) {
  padding: 5px 0;
  color: #47556d;
  font-size: 12px;
}

.table-area :deep(.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell) {
  background: #fafbfd;
}

.table-area :deep(.el-table .el-table__empty-block) {
  min-height: 120px;
}

.job-no {
  font-family: 'SF Mono', Monaco, Consolas, monospace;
  font-size: 12px;
  color: #44516a;
}

.pagination-bar {
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 8px 16px;
  border-top: 1px solid #f0f2f5;
}

:deep(.el-dialog__body) {
  max-height: 68vh;
  overflow-y: auto;
}

.detail-alert {
  margin-bottom: 12px;
}

.result-guide {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-top: 16px;
  padding: 14px 16px;
  border: 1px solid #c7eadb;
  border-radius: 5px;
  background: #f1fbf6;
}

.result-text strong,
.result-text span {
  display: block;
}

.result-text strong {
  color: #2f6e4f;
  font-size: 14px;
}

.result-text span {
  margin-top: 4px;
  color: #708277;
  font-size: 12px;
}

@media (max-width: 760px) {
  .model-summary {
    grid-template-columns: 1fr;
  }

  .card-header {
    flex-wrap: wrap;
  }
}
</style>
