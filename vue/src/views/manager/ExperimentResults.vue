<template>
  <div class="results-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><Picture /></el-icon></div>
      <div class="page-info">
        <div class="page-title">实验结果可视化</div>
        <div class="page-subtitle">统一查看训练与推理生成的异常定位图，结果与算法、数据集和原始任务保持可追溯关联</div>
      </div>
      <div class="header-stat">
        <strong>{{ state.total }}</strong>
        <span>批可视化实验</span>
      </div>
    </div>

    <div class="filter-card">
      <div class="filters">
        <el-select v-model="filters.sourceType" clearable placeholder="全部阶段" @change="search">
          <el-option v-for="item in state.options.sourceTypes" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="filters.algorithmId" clearable filterable placeholder="全部算法" @change="search">
          <el-option
            v-for="item in state.options.algorithms" :key="item.id"
            :label="item.abbreviation ? `${item.name} (${item.abbreviation})` : item.name" :value="item.id"
          />
        </el-select>
        <el-select v-model="filters.datasetId" clearable filterable placeholder="全部数据集" @change="search">
          <el-option v-for="item in state.options.datasets" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button @click="resetFilters">重置筛选</el-button>
        <el-tooltip content="刷新实验结果" placement="top">
          <el-button :icon="Refresh" circle :loading="state.loading" @click="loadRuns" />
        </el-tooltip>
      </div>
    </div>

    <div v-loading="state.loading" class="run-grid">
      <article v-for="run in state.runs" :key="`${run.sourceType}-${run.id}`" class="run-card">
        <div class="run-topline">
          <el-tag :type="run.sourceType === 'TRAINING' ? 'primary' : 'success'" effect="light" round>
            {{ run.sourceType === 'TRAINING' ? '训练结果' : '推理结果' }}
          </el-tag>
          <span>{{ dateTime(run.finishedAt) }}</span>
        </div>
        <div class="algorithm-line">
          <span class="algorithm-mark">{{ initials(run.algorithmAbbreviation || run.algorithmName) }}</span>
          <div class="algorithm-info">
            <h3 :title="run.algorithmName || ''">{{ run.algorithmName || '未知算法' }}</h3>
            <p>{{ run.datasetName || '未知数据集' }}</p>
          </div>
        </div>
        <div class="class-list">
          <el-tag v-for="name in run.classes.slice(0, 4)" :key="name" size="small" effect="plain">{{ name }}</el-tag>
          <span v-if="run.classes.length > 4" class="more-classes">+{{ run.classes.length - 4 }}</span>
          <span v-if="!run.classes.length" class="no-class">未记录类别</span>
        </div>
        <div class="run-metrics">
          <div><strong>{{ run.imageCount }}</strong><span>结果图片</span></div>
          <div><strong>{{ fileSize(run.totalBytes) }}</strong><span>存储大小</span></div>
          <div><strong>{{ shortNo(run.jobNo) }}</strong><span>任务编号</span></div>
        </div>
        <div class="run-actions">
          <el-button type="primary" :disabled="!run.imageCount" @click="openRun(run)">查看结果</el-button>
          <el-button
            :icon="Download"
            :loading="state.downloading === `${run.sourceType}-${run.id}`"
            :disabled="!run.imageCount"
            @click="downloadAll(run)"
          >
            下载全部
          </el-button>
        </div>
      </article>
    </div>

    <el-empty v-if="!state.loading && !state.runs.length" description="当前筛选条件下暂无可视化实验结果" />
    <el-pagination
      v-if="state.total > state.pageSize" class="pagination" background
      layout="total, prev, pager, next" :total="state.total" :page-size="state.pageSize"
      v-model:current-page="state.pageNum" @current-change="loadRuns"
    />

    <el-drawer v-model="state.drawerVisible" size="82%" destroy-on-close class="result-drawer">
      <template #header>
        <div v-if="state.activeRun" class="drawer-title">
          <div class="drawer-title-text">
            <el-tag :type="state.activeRun.sourceType === 'TRAINING' ? 'primary' : 'success'" effect="light" round>
              {{ state.activeRun.sourceType === 'TRAINING' ? '训练结果' : '推理结果' }}
            </el-tag>
            <h2>{{ state.activeRun.algorithmName }} · {{ state.activeRun.datasetName }}</h2>
          </div>
          <el-button
            type="primary"
            plain
            :icon="Download"
            :loading="Boolean(state.downloading)"
            @click="downloadAll(state.activeRun)"
          >
            保存全部结果
          </el-button>
        </div>
      </template>

      <div v-loading="state.imagesLoading" class="image-grid">
        <figure v-for="(image, index) in state.images" :key="image.key" class="image-card">
          <el-image
            :src="imageUrl(state.activeRun, image)"
            :preview-src-list="previewUrls" :initial-index="index"
            fit="cover" loading="lazy" preview-teleported
          >
            <template #error><div class="image-error">图片读取失败</div></template>
          </el-image>
          <figcaption>
            <div class="caption-text">
              <strong :title="image.name">{{ image.name }}</strong>
              <span>{{ fileSize(image.sizeBytes) }}</span>
            </div>
            <el-button link type="primary" @click="downloadOne(image)">下载</el-button>
          </figcaption>
        </figure>
      </div>
      <el-empty v-if="!state.imagesLoading && !state.images.length" description="该任务没有可读取的图片" />
      <el-pagination
        v-if="state.imageTotal > state.imagePageSize" class="pagination" background
        layout="total, prev, pager, next" :total="state.imageTotal" :page-size="state.imagePageSize"
        v-model:current-page="state.imagePageNum" @current-change="loadImages"
      />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive } from 'vue'
import { useRoute } from 'vue-router'
import { Download, Picture, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'
import { API_BASE_URL } from '@/utils/auth'

const state = reactive({
  options: { algorithms: [], datasets: [], sourceTypes: [] },
  runs: [], total: 0, pageNum: 1, pageSize: 12, loading: false,
  drawerVisible: false, activeRun: null, images: [], imageTotal: 0,
  imagePageNum: 1, imagePageSize: 24, imagesLoading: false, downloading: '',
})
const route = useRoute()
const initialSource = ['TRAINING', 'INFERENCE'].includes(String(route.query.sourceType || '').toUpperCase())
  ? String(route.query.sourceType).toUpperCase() : ''
const filters = reactive({ sourceType: initialSource, algorithmId: null, datasetId: null })

const endpoint = (run, suffix = '') => (
  `/experiment-results/runs/${run.sourceType}/${run.id}${suffix}`
)
const previewUrls = computed(() => state.images.map(image => imageUrl(state.activeRun, image)))
const imageUrl = (run, image) => `${API_BASE_URL}${endpoint(run, `/images/${image.key}`)}`
const shortNo = value => value ? value.slice(0, 8) : '--'
const initials = value => String(value || '?').replace(/[^A-Za-z0-9]/g, '').slice(0, 4).toUpperCase() || '?'
const dateTime = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '--'
const fileSize = value => {
  const bytes = Number(value || 0)
  if (!bytes) return '未记录'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`
}
const saveBlob = (blob, name) => {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

const loadOptions = async () => {
  const response = await request.get('/experiment-results/options')
  state.options = response.data
}
const loadRuns = async () => {
  state.loading = true
  try {
    const response = await request.get('/experiment-results/runs', {
      params: { ...filters, pageNum: state.pageNum, pageSize: state.pageSize },
    })
    state.runs = response.data.list
    state.total = response.data.total
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '实验结果读取失败')
  } finally { state.loading = false }
}
const search = () => { state.pageNum = 1; loadRuns() }
const resetFilters = () => {
  Object.assign(filters, { sourceType: '', algorithmId: null, datasetId: null })
  search()
}
const openRun = async run => {
  state.activeRun = run
  state.imagePageNum = 1
  state.drawerVisible = true
  await loadImages()
}
const loadImages = async () => {
  if (!state.activeRun) return
  state.imagesLoading = true
  try {
    const response = await request.get(endpoint(state.activeRun, '/images'), {
      params: { pageNum: state.imagePageNum, pageSize: state.imagePageSize },
    })
    state.images = response.data.list
    state.imageTotal = response.data.total
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '实验图片读取失败')
  } finally { state.imagesLoading = false }
}
const downloadAll = async run => {
  const key = `${run.sourceType}-${run.id}`
  state.downloading = key
  try {
    const blob = await request.get(endpoint(run, '/download'), {
      responseType: 'blob', timeout: 300000,
    })
    saveBlob(blob, `${run.algorithmAbbreviation || 'algorithm'}-${run.datasetName || 'dataset'}-${shortNo(run.jobNo)}.zip`)
    ElMessage.success('实验结果压缩包已生成')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '实验结果下载失败')
  } finally { state.downloading = '' }
}
const downloadOne = async image => {
  try {
    const blob = await request.get(endpoint(state.activeRun, `/images/${image.key}/download`), {
      responseType: 'blob', timeout: 120000,
    })
    saveBlob(blob, image.name)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '图片下载失败')
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), loadRuns()])
})
</script>

<style lang="scss" scoped>
.results-page {
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

.header-stat {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  padding-left: 16px;
  border-left: 1px solid #eef0f4;
  flex-shrink: 0;
}

.header-stat strong {
  color: #1a73e8;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.2;
}

.header-stat span {
  margin-top: 2px;
  color: #9097a5;
  font-size: 11px;
}

.filter-card {
  background: #ffffff;
  border-radius: 5px;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.06);
}

.filters {
  min-height: 42px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  flex-wrap: wrap;
}

.filters :deep(.el-select) {
  width: 200px;
}

.run-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  min-height: 200px;
}

.run-card {
  display: flex;
  flex-direction: column;
  padding: 16px;
  border: 1px solid #e5ebf0;
  border-radius: 5px;
  background: #ffffff;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.06);
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

.run-card:hover {
  border-color: #c6d8f2;
  box-shadow: 0 4px 16px rgba(30, 73, 128, 0.1);
}

.run-topline {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  color: #9097a5;
  font-size: 12px;
}

.algorithm-line {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 14px 0 12px;
}

.algorithm-mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  border-radius: 5px;
  color: #1a73e8;
  background: #eaf3ff;
  font-weight: 700;
  font-size: 12px;
  letter-spacing: 0.02em;
}

.algorithm-info {
  flex: 1;
  min-width: 0;
}

.algorithm-info h3 {
  margin: 0 0 4px;
  color: #263247;
  font-size: 15px;
  font-weight: 600;
  line-height: 20px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.algorithm-info p {
  margin: 0;
  color: #788596;
  font-size: 12px;
  line-height: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.class-list {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 26px;
  color: #8a95a3;
  font-size: 12px;
  flex-wrap: wrap;
}

.more-classes {
  color: #9097a5;
  font-size: 12px;
}

.no-class {
  color: #b6bcc7;
  font-size: 12px;
}

.run-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr 1.2fr;
  margin: 14px 0;
  padding: 12px 0;
  border-top: 1px solid #eef0f4;
  border-bottom: 1px solid #eef0f4;
}

.run-metrics div {
  min-width: 0;
  padding: 0 10px;
  border-right: 1px solid #eef0f4;
}

.run-metrics div:first-child {
  padding-left: 0;
}

.run-metrics div:last-child {
  border-right: 0;
  padding-right: 0;
}

.run-metrics strong,
.run-metrics span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
}

.run-metrics strong {
  color: #263247;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}

.run-metrics span {
  margin-top: 4px;
  color: #929dab;
  font-size: 11px;
}

.run-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: auto;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  padding: 8px 16px;
}

.drawer-title {
  display: flex;
  width: 100%;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding-right: 22px;
}

.drawer-title-text {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.drawer-title h2 {
  margin: 0;
  color: #263247;
  font-size: 16px;
  font-weight: 600;
  line-height: 22px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  min-height: 220px;
}

.image-card {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  border: 1px solid #e5eaef;
  border-radius: 5px;
  background: #ffffff;
  transition: box-shadow 0.2s ease;
}

.image-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}

.image-card :deep(.el-image) {
  display: block;
  width: 100%;
  height: 200px;
  background: #edf1f4;
}

.image-error {
  display: grid;
  place-items: center;
  height: 100%;
  color: #9aa4af;
  font-size: 12px;
}

.image-card figcaption {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border-top: 1px solid #eef0f4;
}

.caption-text {
  min-width: 0;
  flex: 1;
}

.image-card figcaption strong,
.image-card figcaption span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.image-card figcaption strong {
  color: #263247;
  font-size: 13px;
  font-weight: 600;
}

.image-card figcaption span {
  margin-top: 3px;
  color: #9aa3ad;
  font-size: 11px;
}

@media (max-width: 1180px) {
  .run-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .image-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 760px) {
  .page-header {
    flex-wrap: wrap;
  }

  .header-stat {
    display: none;
  }

  .run-grid,
  .image-grid {
    grid-template-columns: 1fr;
  }

  .filters :deep(.el-select) {
    width: 100%;
  }
}
</style>
