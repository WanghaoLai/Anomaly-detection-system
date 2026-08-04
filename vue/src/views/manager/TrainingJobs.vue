<template>
  <div class="training-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><DataLine /></el-icon></div>
      <div class="page-info">
        <div class="page-title">算法训练</div>
        <div class="page-subtitle">
          {{ data.user.role === '管理员' ? '全部训练任务' : '我的训练任务' }} · 共 {{ data.total }} 项
        </div>
      </div>
      <div class="header-actions">
        <el-tag type="info" effect="plain">每人上限 {{ data.options.maxPendingJobs || '--' }}</el-tag>
        <el-tag type="info" effect="plain">并发 {{ data.options.maxConcurrentJobs || '--' }}</el-tag>
        <el-tooltip content="刷新任务列表" placement="top">
          <el-button :icon="Refresh" circle :loading="data.loading" @click="loadJobs" />
        </el-tooltip>
        <el-button type="primary" :icon="Plus" @click="openCreate">创建训练</el-button>
      </div>
    </div>

    <div class="content-card">
      <div class="toolbar">
        <el-select v-model="data.statusFilter" clearable placeholder="全部状态" style="width: 160px" @change="filterChanged">
          <el-option v-for="item in statusOptions" :key="item" :label="statusLabel(item)" :value="item" />
        </el-select>
        <el-select
          v-if="data.user.role === '管理员'"
          v-model="data.archiveState"
          style="width: 140px"
          @change="filterChanged"
        >
          <el-option label="未归档" value="active" />
          <el-option label="已归档" value="archived" />
          <el-option label="全部任务" value="all" />
        </el-select>
        <span class="refresh-hint">列表每 5 秒校准；打开任务详情后通过 SSE 接收实时日志与指标</span>
      </div>

      <div class="table-area">
        <el-table :data="data.jobs" stripe height="100%" empty-text="暂无训练任务" v-loading="data.loading">
          <el-table-column label="任务编号" min-width="190">
            <template #default="{ row }">
              <el-button link type="primary" @click="showDetail(row)">{{ shortJobNo(row.jobNo) }}</el-button>
              <el-tag v-if="row.archivedAt" size="small" type="info" effect="plain" round>已归档</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="算法" prop="algorithmName" min-width="170" show-overflow-tooltip />
          <el-table-column label="数据集" prop="datasetName" width="110" />
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" effect="light" round>{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="尝试" width="70" align="center">
            <template #default="{ row }">#{{ row.attempt }}</template>
          </el-table-column>
          <el-table-column label="GPU" width="70" align="center">
            <template #default="{ row }">{{ row.assignedGpu ?? '--' }}</template>
          </el-table-column>
          <el-table-column label="提交时间" width="170">
            <template #default="{ row }">{{ formatTime(row.submittedAt) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="260" fixed="right" align="center">
            <template #default="{ row }">
              <div class="action-row">
                <el-button class="action-item" link type="primary" @click="showDetail(row)">详情</el-button>
                <el-button v-if="row.status === 'QUEUED'" class="action-item" link type="warning" @click="cancelJob(row)">取消</el-button>
                <el-button v-if="['STARTING','RUNNING'].includes(row.status)" class="action-item" link type="danger" @click="stopJob(row)">停止</el-button>
                <el-button v-if="terminalStatuses.includes(row.status) && !row.archivedAt" class="action-item" link type="success" @click="retryJob(row)">重试</el-button>
                <el-dropdown
                  v-if="data.user.role === '管理员' && terminalStatuses.includes(row.status)"
                  class="action-item"
                  trigger="click"
                  @command="command => adminCommand(command, row)"
                >
                  <el-button link type="primary">更多</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item v-if="!row.archivedAt" command="archive">归档任务</el-dropdown-item>
                      <el-dropdown-item v-else command="restore">恢复归档</el-dropdown-item>
                      <el-dropdown-item
                        command="cleanup"
                        :disabled="row.cleanupStatus === 'CLEANED'"
                      >
                        清理远程产物
                      </el-dropdown-item>
                      <el-dropdown-item
                        v-if="row.archivedAt"
                        command="hard-delete"
                        divided
                        :disabled="row.cleanupStatus !== 'CLEANED'"
                      >
                        彻底删除
                      </el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="pagination-bar">
        <el-pagination
          v-model:current-page="data.pageNum"
          v-model:page-size="data.pageSize"
          background
          layout="total, prev, pager, next"
          :total="data.total"
          @current-change="loadJobs"
        />
      </div>
    </div>

    <el-dialog v-model="data.createVisible" title="创建训练任务" align="center" width="680px" :close-on-click-modal="false">
      <el-form ref="createFormRef" :model="data.createForm" label-width="120px">
        <el-form-item label="算法" required>
          <el-select v-model="data.createForm.algorithmId" style="width: 100%" placeholder="请选择白名单算法" @change="algorithmChanged">
            <el-option
              v-for="item in data.options.algorithms"
              :key="item.id"
              :label="`${item.abbreviation} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="数据集" required>
          <el-select v-model="data.createForm.datasetId" style="width: 100%" placeholder="请选择数据集" @change="datasetChanged">
            <el-option v-for="item in data.options.datasets" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="GPU">
          <el-select v-model="data.createForm.requestedGpu" clearable style="width: 100%" placeholder="自动选择满足显存要求的 GPU">
            <el-option v-for="gpu in data.options.gpuOptions" :key="gpu" :label="`GPU ${gpu}`" :value="gpu" />
          </el-select>
          <div class="form-help">留空时由系统根据租约和真实剩余显存自动选择。</div>
        </el-form-item>

        <el-divider content-position="left">训练参数</el-divider>
        <template v-if="schemaFields.length">
          <el-form-item
            v-for="field in schemaFields"
            :key="field.name"
            :label="field.schema.title || field.name"
            :required="requiredFields.includes(field.name)"
          >
            <el-select
              v-if="field.schema.type === 'array'"
              v-model="data.createForm.parameters[field.name]"
              multiple
              filterable
              collapse-tags
              collapse-tags-tooltip
              placeholder="留空表示全部"
              style="width: 100%"
            >
              <el-option
                v-for="option in field.schema.items?.enum || []"
                :key="option"
                :label="option"
                :value="option"
              />
            </el-select>
            <div class="form-help" v-if="field.schema.type === 'array'">
              留空表示全部；已选 {{ (data.createForm.parameters[field.name] || []).length }} 项
            </div>
            <el-input-number
              v-else-if="field.schema.type === 'integer'"
              v-model="data.createForm.parameters[field.name]"
              :min="field.schema.minimum"
              :max="field.schema.maximum"
              :step="1"
              style="width: 100%"
            />
            <el-input-number
              v-else-if="field.schema.type === 'number'"
              v-model="data.createForm.parameters[field.name]"
              :min="field.schema.minimum ?? field.schema.exclusiveMinimum"
              :max="field.schema.maximum"
              :step="numberStep(field.schema)"
              :precision="field.name === 'learning_rate' ? 6 : 3"
              style="width: 100%"
            />
            <el-input v-else v-model="data.createForm.parameters[field.name]" />
            <div class="form-help" v-if="field.schema.minimum !== undefined || field.schema.maximum !== undefined">
              允许范围：{{ field.schema.minimum ?? `>${field.schema.exclusiveMinimum}` }} ～ {{ field.schema.maximum }}
            </div>
          </el-form-item>
        </template>
        <el-empty v-else description="请先选择算法" :image-size="60" />
      </el-form>
      <template #footer>
        <el-button @click="data.createVisible = false">取消</el-button>
        <el-button type="primary" :loading="data.submitting" @click="submitJob">提交任务</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="data.detailVisible"
      title="训练任务详情"
      size="78%"
      destroy-on-close
      @closed="closeStream"
    >
      <template v-if="data.detail.id">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="任务编号" :span="2">{{ data.detail.jobNo }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType(data.detail.status)" effect="light" round>{{ statusLabel(data.detail.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="算法">{{ data.detail.algorithmName }}</el-descriptions-item>
          <el-descriptions-item label="数据集">{{ data.detail.datasetName }}</el-descriptions-item>
          <el-descriptions-item label="GPU">{{ data.detail.assignedGpu ?? '--' }}</el-descriptions-item>
          <el-descriptions-item label="尝试次数">#{{ data.detail.attempt }}</el-descriptions-item>
          <el-descriptions-item label="退出码">{{ data.detail.exitCode ?? '--' }}</el-descriptions-item>
          <el-descriptions-item label="产物状态">
            <el-tag :type="data.detail.cleanupStatus === 'CLEANED' ? 'info' : 'success'" effect="light" round>
              {{ data.detail.cleanupStatus === 'CLEANED' ? '已清理' : '已保留' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="训练进度" :span="3">
            <div class="progress-cell">
              <el-progress
                :percentage="Math.round(data.detail.progressPercent || 0)"
                :status="data.detail.status === 'SUCCEEDED' ? 'success' : undefined"
              />
              <span>
                epoch {{ data.detail.currentEpoch || 0 }} / {{ data.detail.totalEpochs || '--' }}
              </span>
            </div>
          </el-descriptions-item>
          <el-descriptions-item label="运行目录" :span="3">{{ data.detail.remoteRunDir || '--' }}</el-descriptions-item>
          <el-descriptions-item v-if="data.detail.failureReason" label="失败原因" :span="3">
            <el-tag v-if="data.detail.failureCode" type="danger" size="small">
              {{ failureCodeLabel(data.detail.failureCode) }}
            </el-tag>
            <span class="failure-reason">{{ data.detail.failureReason }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <div class="reliability-bar">
          <div>
            最长运行 {{ durationLabel(data.detail.timeoutSeconds) }}
            · 产物保留策略 {{ data.options.artifactRetentionDays || '--' }} 天
            <span v-if="data.detail.archivedAt"> · 已归档于 {{ formatTime(data.detail.archivedAt) }}</span>
          </div>
          <el-button
            v-if="data.user.role === '管理员' && terminalStatuses.includes(data.detail.status) && data.detail.cleanupStatus !== 'CLEANED'"
            type="danger"
            plain
            size="small"
            :loading="data.cleaning"
            @click="cleanupArtifacts"
          >
            清理远程产物
          </el-button>
        </div>

        <el-tabs v-model="data.detailTab" class="detail-tabs">
          <el-tab-pane name="monitor">
            <template #label>
              <span>实时监控</span>
              <span class="stream-dot" :class="data.streamState"></span>
            </template>
            <div class="monitor-grid">
              <div class="monitor-card">
                <div class="monitor-title">
                  每轮 AUROC
                  <span class="monitor-unit">%</span>
                </div>
                <svg class="metric-chart" viewBox="0 0 640 220" role="img" aria-label="每轮 AUROC 曲线">
                  <line x1="46" y1="12" x2="46" y2="190" class="chart-axis" />
                  <line x1="46" y1="190" x2="624" y2="190" class="chart-axis" />
                  <line v-for="y in [46,82,118,154]" :key="y" x1="46" :y1="y" x2="624" :y2="y" class="chart-grid" />
                  <text x="4" y="18" class="chart-label">100%</text>
                  <text x="18" y="194" class="chart-label">0%</text>
                  <polyline
                    v-for="series in aucChart.series"
                    :key="series.name"
                    :points="series.points"
                    fill="none"
                    :stroke="series.color"
                    stroke-width="3"
                  />
                  <text x="46" y="212" class="chart-label">epoch 1</text>
                  <text x="570" y="212" class="chart-label">epoch {{ data.detail.totalEpochs || '--' }}</text>
                </svg>
                <div class="chart-legend">
                  <span v-for="series in aucChart.series" :key="series.name">
                    <i :style="{ background: series.color }"></i>{{ series.label }}
                  </span>
                  <span v-if="!aucChart.hasData" class="empty-chart">等待首轮评估指标</span>
                </div>
              </div>

              <div class="monitor-card">
                <div class="monitor-title">每轮训练损失</div>
                <svg class="metric-chart" viewBox="0 0 640 220" role="img" aria-label="每轮训练损失曲线">
                  <line x1="46" y1="12" x2="46" y2="190" class="chart-axis" />
                  <line x1="46" y1="190" x2="624" y2="190" class="chart-axis" />
                  <line v-for="y in [46,82,118,154]" :key="y" x1="46" :y1="y" x2="624" :y2="y" class="chart-grid" />
                  <text x="4" y="18" class="chart-label">{{ lossChart.maxLabel }}</text>
                  <text x="18" y="194" class="chart-label">0</text>
                  <polyline
                    v-for="series in lossChart.series"
                    :key="series.name"
                    :points="series.points"
                    fill="none"
                    :stroke="series.color"
                    stroke-width="3"
                  />
                  <text x="46" y="212" class="chart-label">epoch 1</text>
                  <text x="570" y="212" class="chart-label">epoch {{ data.detail.totalEpochs || '--' }}</text>
                </svg>
                <div class="chart-legend">
                  <span v-for="series in lossChart.series" :key="series.name">
                    <i :style="{ background: series.color }"></i>{{ series.label }}
                  </span>
                  <span v-if="!lossChart.hasData" class="empty-chart">等待首轮训练损失</span>
                </div>
              </div>
            </div>

            <div class="log-card">
              <div class="monitor-title">
                实时日志
                <el-tag size="small" :type="streamTagType">{{ streamLabel }}</el-tag>
                <span class="log-count">最近 {{ data.detail.logs?.length || 0 }} 行</span>
              </div>
              <div ref="logConsoleRef" class="log-console">
                <div
                  v-for="log in data.detail.logs"
                  :key="log.id"
                  class="log-line"
                  :class="`log-${String(log.stream || 'stdout').toLowerCase()}`"
                >
                  <span class="log-sequence">{{ String(log.sequence).padStart(4, '0') }}</span>
                  <span>{{ log.content }}</span>
                </div>
                <div v-if="!data.detail.logs?.length" class="log-empty">等待远程训练输出……</div>
              </div>
            </div>
          </el-tab-pane>
          <el-tab-pane label="配置">
            <pre class="json-block">{{ formatJson(data.detail.config) }}</pre>
          </el-tab-pane>
          <el-tab-pane :label="`事件 (${data.detail.events?.length || 0})`">
            <el-timeline>
              <el-timeline-item
                v-for="event in data.detail.events"
                :key="event.sequence"
                :timestamp="formatTime(event.createdAt)"
              >
                <strong>{{ event.type }}</strong> · {{ event.message }}
              </el-timeline-item>
            </el-timeline>
          </el-tab-pane>
          <el-tab-pane :label="`指标 (${data.detail.metrics?.length || 0})`">
            <el-table :data="data.detail.metrics" size="small">
              <el-table-column prop="name" label="指标" />
              <el-table-column label="值">
                <template #default="{ row }">{{ metricValue(row) }}</template>
              </el-table-column>
              <el-table-column prop="epoch" label="最佳 epoch" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`产物 (${data.detail.artifacts?.length || 0})`">
            <el-table :data="data.detail.artifacts" size="small" max-height="420">
              <el-table-column label="角色" width="150">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain">{{ artifactRoleLabel(row.role) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="名称" min-width="220" show-overflow-tooltip />
              <el-table-column label="大小" width="100">
                <template #default="{ row }">{{ fileSize(row.sizeBytes) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="100" align="center">
                <template #default="{ row }">
                  <el-button
                    link
                    type="primary"
                    :disabled="!row.downloadable || data.detail.cleanupStatus === 'CLEANED'"
                    @click="downloadArtifact(row)"
                  >
                    下载
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`审计 (${data.detail.audits?.length || 0})`">
            <el-timeline>
              <el-timeline-item
                v-for="audit in data.detail.audits"
                :key="audit.id"
                :timestamp="formatTime(audit.createdAt)"
                :type="audit.result === 'FAILED' ? 'danger' : 'primary'"
              >
                <strong>{{ auditActionLabel(audit.action) }}</strong>
                · {{ audit.actorRole || '系统' }}
                <span v-if="audit.actorId"> #{{ audit.actorId }}</span>
                <div class="audit-message">{{ audit.message || '--' }}</div>
              </el-timeline-item>
            </el-timeline>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { DataLine, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
import { API_BASE_URL } from '@/utils/auth'

const createFormRef = ref()
const logConsoleRef = ref()
const terminalStatuses = ['SUCCEEDED', 'FAILED', 'STOPPED', 'LOST']
const statusOptions = ['QUEUED', 'STARTING', 'RUNNING', 'STOPPING', ...terminalStatuses]
let refreshTimer = null
let eventSource = null
let resizeObserver = null

const PAGE_HEADER_H = 58
const TOOLBAR_H = 48
const PAGINATION_H = 48
const PAGE_GAP = 10
const TABLE_HEADER_H = 40
const ROW_H = 40

const calcPageSize = () => {
  const available = window.innerHeight - 80 - PAGE_HEADER_H - PAGE_GAP - TOOLBAR_H - PAGINATION_H - TABLE_HEADER_H - 2
  return Math.max(5, Math.floor(available / ROW_H))
}

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  options: { algorithms: [], datasets: [], gpuOptions: [] },
  jobs: [],
  total: 0,
  pageNum: 1,
  pageSize: calcPageSize(),
  statusFilter: '',
  archiveState: 'active',
  loading: false,
  submitting: false,
  cleaning: false,
  createVisible: false,
  detailVisible: false,
  detailTab: 'monitor',
  streamState: 'closed',
  detail: {},
  createForm: { algorithmId: null, datasetId: null, requestedGpu: null, parameters: {} },
})

const selectedAlgorithm = computed(() =>
  data.options.algorithms?.find(item => item.id === data.createForm.algorithmId)
)

const parameterSchema = computed(() => {
  const datasetSchemas = selectedAlgorithm.value?.datasetParameterSchemas || {}
  const value = datasetSchemas[String(data.createForm.datasetId)]
    || selectedAlgorithm.value?.parameterSchema
  if (!value) return {}
  if (typeof value === 'object') return value
  try { return JSON.parse(value) } catch { return {} }
})

const schemaFields = computed(() =>
  Object.entries(parameterSchema.value.properties || {}).map(([name, schema]) => ({ name, schema }))
)
const requiredFields = computed(() => parameterSchema.value.required || [])

const statusLabel = value => ({
  QUEUED: '排队中', STARTING: '启动中', RUNNING: '训练中', STOPPING: '停止中',
  SUCCEEDED: '已成功', FAILED: '失败', STOPPED: '已停止', LOST: '失联',
}[value] || value)

const statusType = value => ({
  QUEUED: 'info', STARTING: 'warning', RUNNING: 'primary', STOPPING: 'warning',
  SUCCEEDED: 'success', FAILED: 'danger', STOPPED: 'info', LOST: 'danger',
}[value] || 'info')

const errorMessage = error => error?.response?.data?.msg || error?.message || '操作失败'
const shortJobNo = value => value ? `${value.slice(0, 8)}…${value.slice(-6)}` : '--'
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '--'
const formatJson = value => JSON.stringify(value || {}, null, 2)
const metricValue = row => {
  if (!Number.isFinite(row?.value)) return '--'
  return row.name?.startsWith('train/') ? row.value.toFixed(4) : `${(row.value * 100).toFixed(2)}%`
}
const fileSize = value => value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.round((value || 0) / 1024)} KB`
const numberStep = schema => schema.default && schema.default < 0.01 ? 0.0001 : 0.01
const durationLabel = seconds => {
  if (!seconds) return '--'
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`
  if (seconds % 60 === 0) return `${seconds / 60} 分钟`
  return `${seconds} 秒`
}
const failureCodeLabel = code => ({
  CUDA_OOM: 'CUDA 显存不足',
  DISK_FULL: '磁盘空间不足',
  EXECUTOR_LOST: '执行器失联',
  TIMEOUT: '运行超时',
  ABNORMAL_EXIT: '异常退出',
  LAUNCH_FAILED: '启动失败',
  USER_STOPPED: '用户停止',
  CANCELED: '排队取消',
}[code] || code)
const artifactRoleLabel = role => ({
  BEST_CHECKPOINT: '最佳模型',
  LAST_CHECKPOINT: '最后模型',
  AUXILIARY_CHECKPOINT: '辅助模型',
  TRAIN_CONFIG: '训练配置',
  EXECUTION_COMMAND: '执行参数',
  RUN_MANIFEST: '运行清单',
  TRAIN_LOG: '完整日志',
  EVALUATION_RESULT: '评估结果',
  EVALUATION_VISUALIZATION: '评估图像',
  OTHER: '其他',
}[role] || role)
const auditActionLabel = action => ({
  JOB_CREATE: '创建任务',
  JOB_CANCEL: '取消任务',
  JOB_STOP: '停止任务',
  JOB_RETRY: '重试任务',
  PROCESS_START: '启动进程',
  PROCESS_FINISH: '进程结束',
  PROCESS_LOST: '进程失联',
  LAUNCH_FAILED: '启动失败',
  TIMEOUT: '运行超时',
  ARTIFACT_DOWNLOAD: '下载产物',
  ARTIFACT_CLEANUP: '清理产物',
  JOB_ARCHIVE: '归档任务',
  JOB_RESTORE: '恢复归档',
  JOB_HARD_DELETE: '彻底删除',
}[action] || action)

const makeChart = (definitions, fixedMax = null) => {
  const metrics = data.detail.metrics || []
  const totalEpochs = Math.max(Number(data.detail.totalEpochs) || 1, 1)
  const values = definitions.flatMap(definition =>
    metrics.filter(item => item.name === definition.name && Number.isFinite(item.value))
  )
  const maxValue = fixedMax || Math.max(1, ...values.map(item => item.value * 1.1))
  const series = definitions.map(definition => {
    const items = metrics
      .filter(item => item.name === definition.name && Number.isFinite(item.value) && item.epoch)
      .sort((a, b) => a.epoch - b.epoch)
    const points = items.map(item => {
      const x = totalEpochs === 1 ? 335 : 46 + ((item.epoch - 1) / (totalEpochs - 1)) * 578
      const y = 190 - Math.min(1, Math.max(0, item.value / maxValue)) * 178
      return `${x.toFixed(1)},${y.toFixed(1)}`
    }).join(' ')
    return { ...definition, points }
  })
  return {
    series,
    hasData: values.length > 0,
    maxLabel: maxValue.toFixed(2),
  }
}

const aucChart = computed(() => makeChart([
  { name: 'eval/image_auroc', label: '图像 AUROC', color: '#2f7df6' },
  { name: 'eval/pixel_auroc', label: '像素 AUROC', color: '#19a974' },
], 1))
const lossChart = computed(() => makeChart([
  { name: 'train/segmentation_loss', label: '分割损失', color: '#f59e0b' },
  { name: 'train/binary_loss', label: '二分类损失', color: '#ef5b72' },
]))
const streamLabel = computed(() => ({
  connecting: '连接中', open: '实时连接', closed: '已结束', error: '重连中',
}[data.streamState] || '未连接'))
const streamTagType = computed(() => ({
  connecting: 'warning', open: 'success', closed: 'info', error: 'danger',
}[data.streamState] || 'info'))

const loadOptions = async () => {
  const res = await request.get('/training/options')
  if (res.code !== '200') throw new Error(res.msg)
  data.options = res.data
}

const loadJobs = async (silent = false) => {
  if (!silent) data.loading = true
  try {
    const res = await request.get('/training/jobs', {
      params: {
        pageNum: data.pageNum,
        pageSize: data.pageSize,
        status: data.statusFilter,
        archiveState: data.archiveState,
      },
    })
    if (res.code !== '200') throw new Error(res.msg)
    data.jobs = res.data?.list || []
    data.total = res.data?.total || 0
    if (data.detailVisible && data.detail.id) await loadDetail(data.detail.id)
  } catch (error) {
    if (!silent) ElMessage.error(errorMessage(error))
  } finally {
    data.loading = false
  }
}

const filterChanged = () => {
  data.pageNum = 1
  loadJobs()
}

const resetParameters = () => {
  const parameters = {}
  for (const [name, schema] of Object.entries(parameterSchema.value.properties || {})) {
    parameters[name] = Array.isArray(schema.default) ? [...schema.default] : schema.default
  }
  data.createForm.parameters = parameters
}

const algorithmChanged = () => {
  data.createForm.datasetId = data.options.datasets?.[0]?.id || null
  resetParameters()
}

const datasetChanged = () => resetParameters()

const openCreate = () => {
  data.createForm = { algorithmId: null, datasetId: null, requestedGpu: null, parameters: {} }
  data.createVisible = true
  if (data.options.algorithms?.length === 1) {
    data.createForm.algorithmId = data.options.algorithms[0].id
    algorithmChanged()
  }
}

const validateCreate = () => {
  if (!data.createForm.algorithmId || !data.createForm.datasetId) return '请选择算法和数据集'
  for (const name of requiredFields.value) {
    const value = data.createForm.parameters[name]
    // array 字段允许空数组：类别等字段"留空表示全部"，语义由后端解释。
    if (Array.isArray(value)) continue
    if (value === undefined || value === null || value === '') {
      return `请填写 ${parameterSchema.value.properties?.[name]?.title || name}`
    }
  }
  return ''
}

const submitJob = async () => {
  const invalid = validateCreate()
  if (invalid) return ElMessage.warning(invalid)
  data.submitting = true
  try {
    const res = await request.post('/training/jobs', data.createForm)
    if (res.code !== '200') throw new Error(res.msg)
    ElMessage.success(res.data.status === 'QUEUED' ? '任务已进入队列' : '训练任务已启动')
    data.createVisible = false
    data.pageNum = 1
    await loadJobs()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    data.submitting = false
  }
}

const loadDetail = async id => {
  const res = await request.get(`/training/jobs/${id}`)
  if (res.code !== '200') throw new Error(res.msg)
  data.detail = res.data
}

const showDetail = async row => {
  try {
    await loadDetail(row.id)
    data.detailTab = 'monitor'
    data.detailVisible = true
    await nextTick()
    startStream(row.id)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

const scrollLogsToBottom = () => nextTick(() => {
  if (logConsoleRef.value) {
    logConsoleRef.value.scrollTop = logConsoleRef.value.scrollHeight
  }
})

const closeStream = () => {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  data.streamState = 'closed'
}

const mergeStreamState = payload => {
  if (!payload || payload.id !== data.detail.id) return
  const preservedLogs = data.detail.logs || []
  Object.assign(data.detail, payload)
  if (!payload.logs) data.detail.logs = preservedLogs
}

const appendStreamLog = log => {
  if (!log || !data.detail.id) return
  const logs = data.detail.logs || (data.detail.logs = [])
  if (logs.some(item => item.id === log.id)) return
  logs.push(log)
  if (logs.length > 500) logs.splice(0, logs.length - 500)
  scrollLogsToBottom()
}

const startStream = id => {
  closeStream()
  data.streamState = 'connecting'
  const source = new EventSource(
    `${API_BASE_URL}/training/jobs/${id}/stream`,
    { withCredentials: true },
  )
  eventSource = source
  source.onopen = () => { data.streamState = 'open' }
  source.addEventListener('snapshot', event => {
    const payload = JSON.parse(event.data)
    mergeStreamState(payload)
    scrollLogsToBottom()
  })
  source.addEventListener('state', event => {
    mergeStreamState(JSON.parse(event.data))
  })
  source.addEventListener('log', event => {
    appendStreamLog(JSON.parse(event.data))
  })
  source.addEventListener('done', event => {
    mergeStreamState({ id: data.detail.id, status: JSON.parse(event.data).status })
    closeStream()
  })
  source.onerror = () => {
    if (eventSource === source) data.streamState = 'error'
  }
}

const confirmAction = async (row, action, title, message) => {
  try {
    await ElMessageBox.confirm(message, title, { type: 'warning' })
    const res = await request.post(`/training/jobs/${row.id}/${action}`)
    if (res.code !== '200') throw new Error(res.msg)
    ElMessage.success('操作成功')
    await loadJobs()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

const cancelJob = row => confirmAction(row, 'cancel', '取消任务', '确定取消这个排队任务吗？')
const stopJob = row => confirmAction(row, 'stop', '停止训练', '停止会终止整个远程训练进程组，确定继续吗？')
const retryJob = row => confirmAction(row, 'retry', '重试任务', '将使用原配置创建一次新的排队任务，确定继续吗？')

const archiveJob = row => confirmAction(
  row,
  'archive',
  '归档任务',
  '归档后任务将从默认列表隐藏，日志、指标、产物和审计记录仍会保留。确定继续吗？',
)
const restoreJob = row => confirmAction(
  row,
  'restore',
  '恢复归档',
  '确定将这个任务恢复到默认任务列表吗？',
)

const downloadArtifact = artifact => {
  const link = document.createElement('a')
  link.href = `${API_BASE_URL}/training/jobs/${data.detail.id}/artifacts/${artifact.id}/download`
  link.download = artifact.name
  document.body.appendChild(link)
  link.click()
  link.remove()
}

const cleanupArtifacts = async (row = null) => {
  const target = row?.id ? row : data.detail
  if (!target?.id) return
  data.cleaning = true
  try {
    const preview = await request.get(`/training/jobs/${target.id}/cleanup-preview`)
    if (preview.code !== '200') throw new Error(preview.msg)
    const info = preview.data
    await ElMessageBox.confirm(
      `将永久删除远程目录中的 ${info.artifactCount} 项产物（${fileSize(info.totalBytes)}）。数据库任务、关键日志、指标和审计记录会保留。确定继续吗？`,
      '清理远程训练产物',
      { type: 'warning', confirmButtonText: '确认清理', cancelButtonText: '取消' },
    )
    const res = await request.post(`/training/jobs/${target.id}/cleanup`)
    if (res.code !== '200') throw new Error(res.msg)
    ElMessage.success('远程训练产物已清理')
    if (data.detailVisible && data.detail.id === target.id) {
      await loadDetail(target.id)
    }
    await loadJobs(true)
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  } finally {
    data.cleaning = false
  }
}

const hardDeleteJob = async row => {
  if (!row.archivedAt) {
    return ElMessage.warning('请先归档任务')
  }
  if (row.cleanupStatus !== 'CLEANED') {
    return ElMessage.warning('请先清理远程产物')
  }
  try {
    const reasonResult = await ElMessageBox.prompt(
      '删除后任务详情、日志、指标和产物索引将不可恢复；系统只保留删除审计存根。请输入删除原因。',
      '彻底删除训练任务',
      {
        type: 'error',
        confirmButtonText: '下一步',
        cancelButtonText: '取消',
        inputPlaceholder: '至少 3 个字符',
        inputValidator: value => String(value || '').trim().length >= 3 || '请输入至少 3 个字符的删除原因',
      },
    )
    const confirmation = await ElMessageBox.prompt(
      `请输入完整任务编号确认：${row.jobNo}`,
      '最终确认',
      {
        type: 'error',
        confirmButtonText: '彻底删除',
        cancelButtonText: '取消',
        inputPlaceholder: row.jobNo,
        inputValidator: value => value === row.jobNo || '任务编号不匹配',
      },
    )
    const res = await request.post(`/training/jobs/${row.id}/hard-delete`, {
      confirmJobNo: confirmation.value,
      reason: reasonResult.value.trim(),
    })
    if (res.code !== '200') throw new Error(res.msg)
    if (data.detailVisible && data.detail.id === row.id) {
      closeStream()
      data.detailVisible = false
      data.detail = {}
    }
    ElMessage.success('训练任务已彻底删除，删除审计存根已保留')
    await loadJobs()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

const adminCommand = (command, row) => {
  if (command === 'archive') return archiveJob(row)
  if (command === 'restore') return restoreJob(row)
  if (command === 'cleanup') return cleanupArtifacts(row)
  if (command === 'hard-delete') return hardDeleteJob(row)
}

onMounted(async () => {
  const handleResize = () => {
    const newSize = calcPageSize()
    if (newSize !== data.pageSize) {
      data.pageSize = newSize
      data.pageNum = 1
      loadJobs()
    }
  }
  window.addEventListener('resize', handleResize)
  resizeObserver = handleResize

  try {
    await loadOptions()
    await loadJobs()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
  refreshTimer = window.setInterval(() => loadJobs(true), 5000)
})

onUnmounted(() => {
  window.clearInterval(refreshTimer)
  closeStream()
  if (resizeObserver) window.removeEventListener('resize', resizeObserver)
})
</script>

<style scoped>
.training-page { height: calc(100vh - 80px); display: flex; flex-direction: column; gap: 10px; overflow: hidden; }
.page-header { min-height: 54px; display: flex; align-items: center; gap: 12px; padding: 10px 16px; background: #fff; border-left: 4px solid #1a73e8; border-radius: 5px; box-shadow: 0 0 10px rgba(0,0,0,.06); }
.page-icon { width: 38px; height: 38px; flex: 0 0 38px; display: grid; place-items: center; border-radius: 5px; background: #eaf3ff; color: #1a73e8; font-size: 20px; }
.page-info { flex: 1; min-width: 0; }
.page-title { font-size: 17px; line-height: 22px; font-weight: 700; color: #263247; }
.page-subtitle { margin-top: 2px; font-size: 12px; line-height: 16px; color: #9097a5; }
.header-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.content-card { flex: 1; min-height: 0; display: flex; flex-direction: column; background: #fff; border-radius: 5px; box-shadow: 0 0 10px rgba(0,0,0,.06); overflow: hidden; }
.toolbar { min-height: 42px; padding: 5px 16px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid #f0f2f5; }
.refresh-hint { margin-left: auto; color: #9097a5; font-size: 12px; }
.table-area { flex: 1; min-height: 0; padding: 0 16px; }
.table-area :deep(.el-table) { --el-table-border-color: #eef0f4; --el-table-header-bg-color: #f5f7fa; --el-table-row-hover-bg-color: #f0f5ff; font-size: 13px; border-radius: 0; }
.table-area :deep(.el-table th.el-table__cell) { padding: 6px 0; font-size: 12px; font-weight: 600; color: #38455b; background-color: #f5f7fa; }
.table-area :deep(.el-table td.el-table__cell) { padding: 5px 0; color: #47556d; font-size: 12px; }
.table-area :deep(.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell) { background: #fafbfd; }
.pagination-bar { min-height: 38px; display: flex; justify-content: flex-end; align-items: center; padding: 3px 16px 8px; border-top: 1px solid #f0f2f5; }
.form-help { width: 100%; color: #9097a5; font-size: 12px; line-height: 18px; }
.action-row { display: inline-flex; align-items: center; justify-content: center; gap: 14px; flex-wrap: wrap; }
.action-row .action-item { margin: 0; padding: 0; }
.detail-tabs { margin-top: 18px; }
.progress-cell { width: 100%; display: grid; grid-template-columns: minmax(260px, 1fr) auto; align-items: center; gap: 16px; }
.failure-reason { margin-left: 8px; }
.reliability-bar { min-height: 42px; margin-top: 10px; padding: 8px 12px; display: flex; align-items: center; justify-content: space-between; gap: 16px; border: 1px solid #e8ebf0; border-radius: 6px; background: #f8fafc; color: #667085; font-size: 12px; }
.audit-message { margin-top: 4px; color: #7b8492; }
.stream-dot { display: inline-block; width: 7px; height: 7px; margin-left: 7px; border-radius: 50%; background: #a8abb2; }
.stream-dot.open { background: #20b26b; box-shadow: 0 0 0 3px rgba(32,178,107,.14); }
.stream-dot.connecting { background: #e6a23c; }
.stream-dot.error { background: #f56c6c; }
.monitor-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.monitor-card, .log-card { padding: 14px; border: 1px solid #e8ebf0; border-radius: 8px; background: #fff; }
.monitor-title { display: flex; align-items: center; gap: 9px; color: #303846; font-weight: 700; }
.monitor-unit { margin-left: auto; color: #9097a5; font-size: 12px; font-weight: 400; }
.metric-chart { display: block; width: 100%; height: 210px; margin-top: 8px; }
.chart-axis { stroke: #aab2bf; stroke-width: 1; }
.chart-grid { stroke: #edf0f4; stroke-width: 1; stroke-dasharray: 4 4; }
.chart-label { fill: #8c95a3; font-size: 11px; }
.chart-legend { min-height: 24px; display: flex; justify-content: center; align-items: center; gap: 20px; color: #667085; font-size: 12px; }
.chart-legend span { display: inline-flex; align-items: center; gap: 6px; }
.chart-legend i { width: 18px; height: 3px; border-radius: 2px; }
.chart-legend .empty-chart { color: #a8abb2; }
.log-card { margin-top: 14px; }
.log-count { margin-left: auto; color: #9097a5; font-size: 12px; font-weight: 400; }
.log-console { height: 300px; margin-top: 12px; padding: 10px 12px; overflow: auto; border-radius: 6px; background: #111827; color: #d6deeb; font: 12px/1.65 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.log-line { display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 8px; white-space: pre-wrap; word-break: break-word; }
.log-sequence { color: #64748b; user-select: none; }
.log-progress { color: #80cbc4; }
.log-error { color: #ff8a80; }
.log-empty { padding: 100px 0; color: #64748b; text-align: center; }
.json-block { margin: 0; padding: 14px; max-height: 420px; overflow: auto; border-radius: 6px; background: #f6f8fb; white-space: pre-wrap; word-break: break-word; }
:deep(.el-drawer__body) { padding-top: 8px; }
@media (max-width: 1100px) {
  .monitor-grid { grid-template-columns: 1fr; }
}
</style>
