<template>
  <div class="server-page">
    <div class="card server-header">
      <div class="server-identity">
        <div class="server-icon">
          <el-icon><Monitor /></el-icon>
        </div>
        <div>
          <div class="page-title">服务器信息</div>
          <div class="server-meta">
            <span>{{ data.summary.host }}</span>
            <span class="meta-divider"></span>
            <span>{{ data.summary.gpuCount || 0 }} / {{ data.summary.expectedGpuCount }} 张 GPU</span>
            <span class="meta-divider"></span>
            <span>{{ data.summary.processes.length }} 个计算进程</span>
          </div>
        </div>
      </div>
      <div class="header-actions">
        <div class="update-time">更新于 {{ formatDate(data.summary.lastUpdated) }}</div>
        <el-tag :type="data.summary.online ? 'success' : 'danger'" effect="light" round>
          <span class="status-dot" :class="{ online: data.summary.online }"></span>
          {{ data.summary.online ? '在线' : '离线' }}
        </el-tag>
        <el-tooltip content="立即刷新" placement="top">
          <el-button
            class="refresh-button"
            :icon="Refresh"
            circle
            :loading="data.refreshing"
            @click="refreshAll"
          />
        </el-tooltip>
      </div>
    </div>

    <el-alert
      v-if="data.summary.error"
      class="status-alert"
      :title="data.summary.error"
      type="warning"
      :closable="false"
      show-icon
    />

    <div class="content-panel">
      <el-tabs v-model="data.activeTab" @tab-change="handleTabChange">
        <el-tab-pane name="gpu">
          <template #label>
            <span class="tab-label"><el-icon><Cpu /></el-icon>GPU 状态</span>
          </template>

          <el-skeleton v-if="data.initialLoading" :rows="8" animated />
          <template v-else>
            <div class="gpu-grid">
              <div
                v-for="gpu in displayedGpus"
                :key="gpu.index"
                class="gpu-card"
                :class="{ unavailable: gpu.placeholder }"
              >
                <div class="gpu-card-header">
                  <div>
                    <div class="gpu-index">GPU {{ gpu.index }}</div>
                    <div class="gpu-name">{{ gpu.name }}</div>
                  </div>
                  <el-tag size="small" :type="gpu.placeholder ? 'info' : gpuStatusType(gpu)">
                    {{ gpu.placeholder ? '未连接' : gpuStatusText(gpu) }}
                  </el-tag>
                </div>

                <div class="metric-block">
                  <div class="metric-heading">
                    <span>GPU 利用率</span>
                    <strong>{{ metric(gpu.utilization, '%') }}</strong>
                  </div>
                  <el-progress
                    :percentage="gpu.utilization || 0"
                    :stroke-width="8"
                    :show-text="false"
                    :color="progressColor(gpu.utilization)"
                  />
                </div>

                <div class="metric-block">
                  <div class="metric-heading">
                    <span>显存使用</span>
                    <strong>{{ metric(gpu.memoryUtilization, '%') }}</strong>
                  </div>
                  <el-progress
                    :percentage="gpu.memoryUtilization || 0"
                    :stroke-width="8"
                    :show-text="false"
                    :color="progressColor(gpu.memoryUtilization)"
                  />
                  <div class="memory-detail">
                    {{ gpu.placeholder ? '--' : `${formatMemory(gpu.memoryUsed)} / ${formatMemory(gpu.memoryTotal)}` }}
                  </div>
                </div>

                <div class="gpu-details">
                  <div><span>温度</span><strong>{{ metric(gpu.temperature, '°C') }}</strong></div>
                  <div><span>功耗</span><strong>{{ powerText(gpu) }}</strong></div>
                  <div><span>驱动</span><strong>{{ gpu.driverVersion || '--' }}</strong></div>
                </div>
              </div>
            </div>

            <div class="section-heading">
              <div>
                <div class="section-title">GPU 计算进程</div>
              </div>
              <el-tag type="info" effect="plain">{{ data.summary.processes.length }} 个进程</el-tag>
            </div>

            <el-table
              :data="data.summary.processes"
              stripe
              empty-text="当前没有 GPU 计算进程"
              class="process-table"
            >
              <el-table-column label="GPU" prop="gpuIndex" width="90" align="center">
                <template #default="scope">
                  <el-tag size="small" effect="plain">GPU {{ scope.row.gpuIndex }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="PID" prop="pid" width="110" align="center" />
              <el-table-column label="进程名称" prop="processName" min-width="220" show-overflow-tooltip />
              <el-table-column label="所属账号" prop="username" width="160">
                <template #default="scope">
                  <div class="account-cell"><el-icon><User /></el-icon>{{ scope.row.username }}</div>
                </template>
              </el-table-column>
              <el-table-column label="显存占用" prop="memoryUsed" width="140" align="right">
                <template #default="scope">{{ formatMemory(scope.row.memoryUsed) }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-tab-pane>

        <el-tab-pane name="files">
          <template #label>
            <span class="tab-label"><el-icon><Files /></el-icon>账号文件</span>
          </template>

          <div class="file-toolbar">
            <div class="file-context">
              <div class="account-summary">
                <el-icon><User /></el-icon>
                <span>GPU 账号</span>
                <strong>{{ data.files.account || '--' }}</strong>
              </div>
              <div class="directory-select">
                <span>展示目录</span>
                <el-select
                  v-model="data.selectedRootId"
                  :disabled="data.fileRoots.length <= 1"
                  placeholder="请选择目录"
                  @change="changeFileRoot"
                >
                  <el-option
                    v-for="directory in data.fileRoots"
                    :key="directory.id"
                    :label="directory.name"
                    :value="directory.id"
                  />
                </el-select>
              </div>
            </div>
            <el-tooltip content="刷新当前目录" placement="top">
              <el-button :icon="Refresh" circle :loading="data.filesLoading" @click="loadFiles" />
            </el-tooltip>
          </div>

          <el-alert
            v-if="data.filesError"
            class="file-alert"
            :title="data.filesError"
            type="warning"
            :closable="false"
            show-icon
          />

          <div class="path-bar">
            <el-icon><FolderOpened /></el-icon>
            <el-breadcrumb separator="/">
              <el-breadcrumb-item>
                <button class="breadcrumb-button" @click="navigateToBreadcrumb(-1)">{{ selectedRootName }}</button>
              </el-breadcrumb-item>
              <el-breadcrumb-item v-for="(part, index) in pathParts" :key="`${part}-${index}`">
                <button class="breadcrumb-button" @click="navigateToBreadcrumb(index)">{{ part }}</button>
              </el-breadcrumb-item>
            </el-breadcrumb>
            <el-tooltip content="复制当前文件夹绝对路径" placement="top">
              <el-button
                class="copy-path-button"
                :icon="CopyDocument"
                size="small"
                :disabled="!data.files.absolutePath"
                @click="copyCurrentPath"
              >
                复制路径
              </el-button>
            </el-tooltip>
          </div>

          <el-table
            v-loading="data.filesLoading"
            :data="data.files.items"
            stripe
            empty-text="当前目录没有可显示的文件"
            @row-dblclick="openDirectory"
          >
            <el-table-column label="名称" min-width="300">
              <template #default="scope">
                <button
                  v-if="scope.row.type === 'directory'"
                  class="file-name directory-name"
                  @click="openDirectory(scope.row)"
                >
                  <el-icon><FolderOpened /></el-icon>{{ scope.row.name }}
                </button>
                <div v-else class="file-name">
                  <el-icon><Link v-if="scope.row.type === 'symlink'" /><Document v-else /></el-icon>
                  <span>{{ scope.row.name }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="所属账号" prop="owner" width="150" />
            <el-table-column label="权限" prop="permissions" width="150" />
            <el-table-column label="大小" prop="size" width="120" align="right">
              <template #default="scope">{{ scope.row.type === 'directory' ? '--' : formatBytes(scope.row.size) }}</template>
            </el-table-column>
            <el-table-column label="修改时间" prop="modifiedAt" width="190">
              <template #default="scope">{{ formatDate(scope.row.modifiedAt) }}</template>
            </el-table-column>
          </el-table>

          <div class="file-pagination">
            <span v-if="data.files.truncated" class="truncated-tip">目录条目过多，已按安全上限截断</span>
            <el-pagination
              v-model:current-page="data.files.page"
              v-model:page-size="data.files.pageSize"
              background
              layout="total, prev, pager, next"
              :total="data.files.total"
              @current-change="loadFiles"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="data.user.role === '管理员'" name="conda">
          <template #label>
            <span class="tab-label"><el-icon><SetUp /></el-icon>Conda 环境列表</span>
          </template>

          <div class="conda-toolbar">
            <div>
              <div class="section-title">Conda 环境列表</div>
              <div class="section-subtitle">扫描配置的环境总目录，仅展示包含 conda-meta 的有效环境</div>
            </div>
            <el-tooltip content="刷新 Conda 环境列表" placement="top">
              <el-button
                :icon="Refresh"
                circle
                :loading="data.condaLoading"
                @click="loadCondaEnvironments"
              />
            </el-tooltip>
          </div>

          <el-alert
            v-if="data.condaError"
            class="file-alert"
            :title="data.condaError"
            type="warning"
            :closable="false"
            show-icon
          />

          <div v-if="data.conda.roots.length" class="conda-roots">
            <span class="conda-roots-label">扫描目录</span>
            <el-tooltip
              v-for="root in data.conda.roots"
              :key="root.id"
              :content="root.path"
              placement="top"
            >
              <el-tag :type="root.available ? 'info' : 'danger'" effect="plain">
                {{ root.name }}{{ root.available ? '' : '（不可访问）' }}
              </el-tag>
            </el-tooltip>
          </div>

          <el-table
            v-loading="data.condaLoading"
            :data="data.conda.environments"
            stripe
            empty-text="没有发现可展示的 Conda 环境"
          >
            <el-table-column label="环境名称" prop="name" min-width="180" />
            <el-table-column label="绝对路径" prop="path" min-width="420" show-overflow-tooltip />
            <el-table-column label="来源目录" prop="sourceName" width="180" />
            <el-table-column label="修改时间" width="190">
              <template #default="scope">{{ formatDate(scope.row.modifiedAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="110" align="center" fixed="right">
              <template #default="scope">
                <el-button
                  type="primary"
                  link
                  :icon="CopyDocument"
                  @click="copyText(scope.row.path, `环境 ${scope.row.name} 的绝对路径已复制`)"
                >
                  复制路径
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="conda-footer">
            <span>共 {{ data.conda.total }} 个环境</span>
            <span v-if="data.conda.scannedAt">扫描于 {{ formatDate(data.conda.scannedAt) }}</span>
            <span v-if="data.conda.truncated" class="truncated-tip">环境数量过多，已按安全上限截断</span>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive } from 'vue'
import { CopyDocument, Cpu, Document, Files, FolderOpened, Link, Monitor, Refresh, SetUp, User } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'

const emptySummary = () => ({
  configured: false,
  online: false,
  host: '未配置',
  expectedGpuCount: 4,
  gpuCount: 0,
  gpus: [],
  processes: [],
  lastUpdated: null,
  error: null,
})

const emptyFiles = () => ({
  account: '',
  rootId: '',
  rootName: '',
  path: '',
  absolutePath: '',
  parent: null,
  page: 1,
  pageSize: 20,
  total: 0,
  truncated: false,
  items: [],
})

const emptyConda = () => ({
  source: 'DIRECTORY',
  roots: [],
  environments: [],
  total: 0,
  truncated: false,
  scannedAt: null,
})

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  summary: emptySummary(),
  files: emptyFiles(),
  activeTab: 'gpu',
  initialLoading: true,
  refreshing: false,
  filesLoading: false,
  filesLoaded: false,
  filesError: '',
  fileRoots: [],
  selectedRootId: '',
  conda: emptyConda(),
  condaLoading: false,
  condaLoaded: false,
  condaError: '',
})

let refreshTimer = null

const displayedGpus = computed(() => {
  if (data.summary.gpus.length) return data.summary.gpus
  return Array.from({ length: data.summary.expectedGpuCount || 4 }, (_, index) => ({
    index,
    name: 'NVIDIA GeForce RTX 4090',
    placeholder: true,
    utilization: 0,
    memoryUtilization: 0,
  }))
})

const pathParts = computed(() => data.files.path ? data.files.path.split('/').filter(Boolean) : [])
const selectedRootName = computed(() => {
  const selected = data.fileRoots.find((directory) => directory.id === data.selectedRootId)
  return selected?.name || data.files.rootName || '授权目录'
})

const loadSummary = async (force = false) => {
  if (data.refreshing) return
  data.refreshing = true
  try {
    const res = await request.get('/server/summary', { params: { refresh: force } })
    if (res.code === '200') {
      data.summary = { ...emptySummary(), ...res.data }
    } else {
      data.summary.error = res.msg || '服务器状态获取失败'
    }
  } catch (error) {
    data.summary.online = false
    data.summary.error = '无法获取服务器状态'
  } finally {
    data.initialLoading = false
    data.refreshing = false
  }
}

const loadFiles = async () => {
  if (data.filesLoading) return
  if (!data.summary.configured || !data.summary.online) {
    data.filesError = data.summary.error || 'GPU 服务器当前不可用'
    return
  }
  data.filesLoading = true
  data.filesError = ''
  try {
    const res = await request.get('/server/files', {
      params: {
        root_id: data.selectedRootId,
        path: data.files.path,
        page: data.files.page,
        page_size: data.files.pageSize,
      },
    })
    if (res.code === '200') {
      data.files = { ...emptyFiles(), ...res.data }
      data.filesLoaded = true
    } else {
      data.filesError = res.msg || '文件信息获取失败'
    }
  } catch (error) {
    data.filesError = '无法读取当前账号的文件信息'
  } finally {
    data.filesLoading = false
  }
}

const loadFileRoots = async () => {
  if (data.filesLoading) return
  if (!data.summary.configured || !data.summary.online) {
    data.filesError = data.summary.error || 'GPU 服务器当前不可用'
    return
  }
  data.filesLoading = true
  data.filesError = ''
  let shouldLoadFiles = false
  try {
    const res = await request.get('/server/file-roots')
    if (res.code === '200') {
      data.fileRoots = res.data?.directories || []
      const selectedExists = data.fileRoots.some((item) => item.id === data.selectedRootId)
      data.selectedRootId = selectedExists ? data.selectedRootId : (data.fileRoots[0]?.id || '')
      data.files = { ...emptyFiles(), account: res.data?.account || '' }
      shouldLoadFiles = Boolean(data.selectedRootId)
      if (!shouldLoadFiles) data.filesError = '尚未配置可展示的账号目录'
    } else {
      data.filesError = res.msg || '展示目录获取失败'
    }
  } catch (error) {
    data.filesError = '无法获取账号的授权目录'
  } finally {
    data.filesLoading = false
  }
  if (shouldLoadFiles) await loadFiles()
}

const refreshAll = async () => {
  await loadSummary(true)
  if (data.activeTab === 'files' && data.summary.online) {
    if (data.fileRoots.length) await loadFiles()
    else await loadFileRoots()
  }
  if (data.activeTab === 'conda' && data.summary.online) {
    await loadCondaEnvironments()
  }
  ElMessage.success('服务器信息已刷新')
}

const handleTabChange = (tabName) => {
  if (tabName === 'files' && !data.filesLoaded) loadFileRoots()
  if (tabName === 'conda' && !data.condaLoaded) loadCondaEnvironments()
}

const changeFileRoot = () => {
  data.files = {
    ...emptyFiles(),
    account: data.files.account,
    rootId: data.selectedRootId,
    rootName: selectedRootName.value,
  }
  loadFiles()
}

const openDirectory = (row) => {
  if (!row || row.type !== 'directory') return
  data.files.path = [data.files.path, row.name].filter(Boolean).join('/')
  data.files.page = 1
  loadFiles()
}

const navigateToBreadcrumb = (index) => {
  data.files.path = index < 0 ? '' : pathParts.value.slice(0, index + 1).join('/')
  data.files.page = 1
  loadFiles()
}

const loadCondaEnvironments = async () => {
  if (data.condaLoading) return
  if (!data.summary.configured || !data.summary.online) {
    data.condaError = data.summary.error || 'GPU 服务器当前不可用'
    return
  }
  data.condaLoading = true
  data.condaError = ''
  try {
    const res = await request.get('/server/conda-environments')
    if (res.code === '200') {
      data.conda = { ...emptyConda(), ...res.data }
      data.condaLoaded = true
      const unavailable = data.conda.roots.filter((root) => !root.available)
      if (unavailable.length) {
        data.condaError = `${unavailable.map((root) => root.name).join('、')}不可访问`
      }
    } else {
      data.condaError = res.msg || 'Conda 环境列表获取失败'
    }
  } catch {
    data.condaError = '无法读取 Conda 环境列表'
  } finally {
    data.condaLoading = false
  }
}

const copyText = async (text, successMessage = '绝对路径已复制') => {
  if (!text) {
    ElMessage.warning('当前绝对路径不可用')
    return
  }
  try {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text)
        ElMessage.success(successMessage)
        return
      } catch {
        // 浏览器拒绝 Clipboard API 时继续使用兼容复制方式。
      }
    }

    const textarea = document.createElement('textarea')
    try {
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const copied = document.execCommand('copy')
      if (!copied) throw new Error('copy failed')
    } finally {
      textarea.remove()
    }
    ElMessage.success(successMessage)
  } catch {
    ElMessage.error('路径复制失败，请检查浏览器剪贴板权限')
  }
}

const copyCurrentPath = () => copyText(data.files.absolutePath)

const progressColor = (value = 0) => {
  if (value >= 90) return '#e45656'
  if (value >= 70) return '#e6a23c'
  return '#1a73e8'
}

const gpuStatusType = (gpu) => {
  const high = Math.max(gpu.utilization || 0, gpu.memoryUtilization || 0)
  if ((gpu.temperature || 0) >= 85 || high >= 95) return 'danger'
  if ((gpu.temperature || 0) >= 75 || high >= 80) return 'warning'
  return 'success'
}

const gpuStatusText = (gpu) => {
  const type = gpuStatusType(gpu)
  return type === 'danger' ? '高负载' : type === 'warning' ? '繁忙' : '正常'
}

const metric = (value, unit) => value === null || value === undefined ? '--' : `${value}${unit}`
const formatMemory = (value) => value === null || value === undefined ? '--' : `${Number(value).toLocaleString()} MiB`
const powerText = (gpu) => gpu.placeholder || gpu.powerDraw === null || gpu.powerDraw === undefined
  ? '--'
  : `${gpu.powerDraw} / ${gpu.powerLimit || '--'} W`

const formatBytes = (bytes) => {
  const value = Number(bytes || 0)
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = value / 1024
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[index]}`
}

const formatDate = (value) => {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '--'
  return date.toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  await loadSummary()
  refreshTimer = window.setInterval(() => {
    if (!document.hidden) loadSummary()
  }, 5000)
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style lang="scss" scoped>
.server-page {
  min-width: 0;
}

.server-header {
  min-height: 72px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 14px 18px;
  margin-bottom: 8px;
}

.server-identity,
.header-actions,
.server-meta,
.tab-label,
.account-cell,
.file-toolbar,
.account-summary,
.path-bar,
.file-name {
  display: flex;
  align-items: center;
}

.server-icon {
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  display: grid;
  place-items: center;
  margin-right: 12px;
  border-radius: 5px;
  background: #eaf3ff;
  color: #1a73e8;
  font-size: 23px;
}

.page-title {
  font-size: 18px;
  line-height: 24px;
  font-weight: 700;
  color: #263247;
}

.server-meta {
  flex-wrap: wrap;
  gap: 9px;
  margin-top: 5px;
  color: #7b8494;
  font-size: 12px;
}

.meta-divider {
  width: 1px;
  height: 11px;
  background: #d8dde6;
}

.header-actions {
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.update-time {
  color: #9097a5;
  font-size: 12px;
}

.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 5px;
  border-radius: 50%;
  background: #e45656;
}

.status-dot.online {
  background: #38a169;
}

.status-alert,
.file-alert {
  margin-bottom: 8px;
}

.content-panel {
  min-height: calc(100vh - 168px);
  padding: 6px 16px 16px;
  background: #ffffff;
}

.tab-label {
  gap: 6px;
  padding: 0 4px;
}

.gpu-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 8px 0 22px;
}

.gpu-card {
  min-width: 0;
  padding: 14px;
  border: 1px solid #e3e7ee;
  border-top: 3px solid #1a73e8;
  border-radius: 5px;
  background: #ffffff;
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

.gpu-card:hover {
  border-color: #c6d8f2;
  box-shadow: 0 5px 16px rgba(30, 73, 128, 0.1);
}

.gpu-card.unavailable {
  border-top-color: #aeb6c2;
  background: #fafbfc;
}

.gpu-card-header {
  min-height: 48px;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  padding-bottom: 12px;
  border-bottom: 1px solid #eef0f4;
}

.gpu-index {
  color: #24344f;
  font-size: 15px;
  line-height: 20px;
  font-weight: 700;
}

.gpu-name {
  margin-top: 3px;
  color: #8a93a2;
  font-size: 11px;
  line-height: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-block {
  margin-top: 13px;
}

.metric-heading {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 7px;
  color: #70798a;
  font-size: 12px;
}

.metric-heading strong {
  color: #2d3748;
  font-weight: 600;
}

.memory-detail {
  margin-top: 5px;
  color: #9aa1ad;
  font-size: 10px;
  text-align: right;
}

.gpu-details {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin-top: 14px;
  padding-top: 11px;
  border-top: 1px solid #eef0f4;
}

.gpu-details div {
  min-width: 0;
}

.gpu-details span,
.gpu-details strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gpu-details span {
  color: #9aa1ad;
  font-size: 10px;
}

.gpu-details strong {
  margin-top: 4px;
  color: #38455b;
  font-size: 11px;
  font-weight: 600;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 0 0 10px;
}

.section-title {
  color: #29364b;
  font-size: 15px;
  line-height: 22px;
  font-weight: 700;
}

.section-subtitle {
  margin-top: 3px;
  color: #8a93a2;
  font-size: 12px;
}

.account-cell {
  gap: 6px;
  color: #44516a;
}

.file-toolbar {
  min-height: 42px;
  justify-content: space-between;
  margin-top: 4px;
}

.file-context,
.directory-select {
  display: flex;
  align-items: center;
}

.file-context {
  min-width: 0;
  gap: 24px;
  flex-wrap: wrap;
}

.directory-select {
  gap: 8px;
  color: #747d8c;
  font-size: 13px;
}

.directory-select .el-select {
  width: 190px;
}

.account-summary {
  gap: 7px;
  color: #747d8c;
  font-size: 13px;
}

.account-summary .el-icon {
  color: #1a73e8;
}

.account-summary strong {
  color: #29364b;
}

.path-bar {
  min-height: 42px;
  gap: 9px;
  padding: 8px 12px;
  margin: 7px 0 10px;
  border: 1px solid #e4e8ef;
  border-radius: 5px;
  background: #f7f9fc;
  color: #65738a;
  overflow-x: auto;
}

.path-bar .el-breadcrumb {
  min-width: 0;
  flex: 1;
}

.copy-path-button {
  flex: 0 0 auto;
  margin-left: auto;
}

.breadcrumb-button,
.file-name.directory-name {
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.breadcrumb-button:hover,
.file-name.directory-name:hover {
  color: #1a73e8;
}

.file-name {
  min-width: 0;
  gap: 8px;
  color: #47556d;
}

.file-name .el-icon {
  flex: 0 0 auto;
  color: #6b7f9c;
  font-size: 17px;
}

.file-name.directory-name {
  color: #2f5f9f;
}

.file-name.directory-name .el-icon {
  color: #e3a329;
}

.file-pagination {
  min-height: 52px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  padding-top: 12px;
}

.conda-toolbar {
  min-height: 58px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 6px 0 10px;
}

.conda-roots {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 9px 12px;
  margin-bottom: 10px;
  border: 1px solid #e4e8ef;
  border-radius: 5px;
  background: #f7f9fc;
}

.conda-roots-label {
  margin-right: 4px;
  color: #65738a;
  font-size: 12px;
  font-weight: 600;
}

.conda-footer {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 18px;
  color: #8a93a2;
  font-size: 12px;
}

.truncated-tip {
  margin-right: auto;
  color: #b47716;
  font-size: 12px;
}

@media (max-width: 1100px) {
  .gpu-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .server-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .header-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .gpu-grid {
    grid-template-columns: 1fr;
  }

  .file-pagination {
    align-items: flex-start;
    flex-direction: column;
  }

  .file-toolbar,
  .file-context {
    align-items: flex-start;
  }

  .file-context {
    gap: 8px;
    flex-direction: column;
  }
}
</style>
