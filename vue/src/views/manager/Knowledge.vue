<template>
  <div class="knowledge-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><Collection /></el-icon></div>
      <div>
        <div class="page-title">知识库管理</div>
        <div class="page-subtitle">
          共 {{ data.stats.document_count || 0 }} 个文档，{{ data.stats.chunk_count || 0 }} 个分块
        </div>
      </div>
    </div>

    <div class="knowledge-card">
      <div class="toolbar">
        <div class="toolbar-left">
          <el-upload
            :show-file-list="false"
            :http-request="handleUpload"
            :before-upload="beforeUpload"
            accept=".txt,.md,.markdown,.pdf,.docx,.pptx,.xlsx,.xls,.csv,.html,.htm,.json,.xml,.ipynb,.epub"
          >
            <el-button type="primary" :loading="data.uploading">
              <el-icon><Upload /></el-icon>上传文档
            </el-button>
          </el-upload>
          <el-button :loading="data.checkingHealth" @click="checkHealth(true)">
            检查索引
          </el-button>
          <el-tag
            v-if="data.health.status"
            :type="data.health.healthy ? 'success' : 'danger'"
            effect="plain"
          >
            {{ data.health.healthy ? '索引正常' : `发现 ${data.health.summary?.issue_count || 0} 项问题` }}
          </el-tag>
          <span class="format-hint">支持常见文本、PDF、Word、PPT、Excel 等格式，最大 20MB</span>
        </div>
      </div>

      <div class="table-area">
        <el-table
          :data="data.tableData"
          stripe
          :max-height="tableHeight"
          empty-text=""
        >
          <el-table-column label="文件名" min-width="260">
            <template #default="scope">
              <div class="file-name-cell">
                <el-icon class="file-icon" :class="fileIconClass(scope.row.original_name)">
                  <Document />
                </el-icon>
                <span>{{ scope.row.original_name }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="大小" width="120" align="center">
            <template #default="scope">{{ formatSize(scope.row.file_size) }}</template>
          </el-table-column>
          <el-table-column label="分块数" prop="chunk_count" width="100" align="center" />
          <el-table-column label="上传时间" width="180" align="center">
            <template #default="scope">{{ formatDate(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" align="center" width="100" fixed="right">
            <template #default="scope">
              <div class="action-group">
                <el-button class="action-btn action-btn--delete" size="small" round @click="handleDelete(scope.row.id)">
                  <el-icon><Delete /></el-icon>删除
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="data.tableData.length === 0" class="empty-state">
          <el-icon class="empty-icon"><Collection /></el-icon>
          <div class="empty-title">暂无文档</div>
          <div class="empty-desc">请上传文档构建知识库</div>
        </div>
      </div>
    </div>

    <el-dialog
      v-model="data.previewDialog"
      title="PDF 入库预览"
      width="760px"
      :close-on-click-modal="false"
      @closed="cancelPreview"
    >
      <template v-if="data.previewData.diagnostics">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="识别页数">
            {{ data.previewData.diagnostics.page_count || '未识别' }}
          </el-descriptions-item>
          <el-descriptions-item label="清理页眉/页脚">
            {{ data.previewData.diagnostics.headers_removed || 0 }}/{{ data.previewData.diagnostics.footers_removed || 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="识别标题">
            {{ data.previewData.diagnostics.detected_title_count || 0 }} 个
          </el-descriptions-item>
          <el-descriptions-item label="预计分块">
            {{ data.previewData.diagnostics.chunk_count || 0 }} 个
          </el-descriptions-item>
          <el-descriptions-item label="平均分块">
            {{ data.previewData.diagnostics.average_chunk_tokens || 0 }} Token
          </el-descriptions-item>
          <el-descriptions-item label="清理后字符">
            {{ data.previewData.diagnostics.cleaned_char_count || 0 }}
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-for="warning in data.previewData.warnings || []"
          :key="warning"
          class="preview-warning"
          type="warning"
          :title="warning"
          :closable="false"
          show-icon
        />

        <div v-if="data.previewData.diagnostics.detected_titles?.length" class="detected-titles">
          <span class="preview-label">识别到的标题：</span>
          <el-tag
            v-for="title in data.previewData.diagnostics.detected_titles.slice(0, 8)"
            :key="title"
            size="small"
            effect="plain"
          >{{ title.replace(/^#+\s*/, '') }}</el-tag>
        </div>

        <div class="preview-label">清理后的 Markdown 预览：</div>
        <pre class="markdown-preview">{{ data.previewData.preview_markdown }}</pre>
        <div v-if="data.previewData.preview_truncated" class="preview-truncated">
          内容较长，此处仅展示前 8000 个字符。
        </div>
      </template>
      <template #footer>
        <el-button @click="cancelPreview">取消</el-button>
        <el-button type="primary" :loading="data.uploading" @click="confirmPreview">
          确认构建知识库
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, computed } from "vue"
import { Collection, Upload, Document, Delete } from "@element-plus/icons-vue"
import request from "@/utils/request"
import { ElMessage, ElMessageBox } from "element-plus"

const data = reactive({
  tableData: [],
  uploading: false,
  checkingHealth: false,
  stats: {},
  health: {},
  previewDialog: false,
  previewData: {},
  pendingUpload: null,
})

const PAGE_HEADER_H = 54
const TOOLBAR_H = 52
const PAGE_PADDING = 10
const TABLE_HEADER_H = 36
const TABLE_BODY_H = computed(() => Math.max(data.tableData.length, 1) * 44)

const tableHeight = computed(() =>
  PAGE_HEADER_H + TOOLBAR_H + PAGE_PADDING + TABLE_HEADER_H + TABLE_BODY_H.value + 2
)

const formatDate = (value) => {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

const fileIconClass = (name) => {
  if (!name) return ''
  const ext = name.split('.').pop().toLowerCase()
  if (ext === 'pdf') return 'icon-pdf'
  if (['docx', 'pptx', 'xlsx', 'xls'].includes(ext)) return 'icon-docx'
  return 'icon-txt'
}

const load = () => {
  request.get('/knowledge/list').then(res => {
    if (res.code === '200') {
      data.tableData = res.data || []
    }
  })
  request.get('/knowledge/stats').then(res => {
    if (res.code === '200') {
      data.stats = res.data || {}
    }
  })
  checkHealth(false)
}

const checkHealth = async (notify = true) => {
  data.checkingHealth = true
  try {
    const res = await request.get('/knowledge/health')
    if (res.code !== '200') {
      if (notify) ElMessage.error(res.msg || '健康检查失败')
      return
    }
    data.health = res.data || {}
    if (notify) {
      if (data.health.healthy) {
        ElMessage.success('MySQL 元数据与 Chroma 索引一致')
      } else {
        ElMessage.warning(`检查完成，发现 ${data.health.summary?.issue_count || 0} 项问题`)
      }
    }
  } catch (e) {
    if (notify) ElMessage.error('健康检查失败')
  } finally {
    data.checkingHealth = false
  }
}

const beforeUpload = (file) => {
  const ext = file.name.split('.').pop().toLowerCase()
  const allowedExtensions = [
    'txt', 'md', 'markdown', 'pdf', 'docx', 'pptx', 'xlsx', 'xls',
    'csv', 'html', 'htm', 'json', 'xml', 'ipynb', 'epub'
  ]
  if (!allowedExtensions.includes(ext)) {
    ElMessage.error('不支持该文件格式，请上传常见文本、PDF、Word、PPT 或 Excel 文档')
    return false
  }
  if (file.size > 20 * 1024 * 1024) {
    ElMessage.error('文件大小不能超过 20MB')
    return false
  }
  return true
}

const uploadDocument = async (options) => {
  data.uploading = true
  const formData = new FormData()
  formData.append('file', options.file)
  try {
    const res = await request.post('/knowledge/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    if (res.code === '200') {
      if (res.data?.unchanged) {
        ElMessage.success('文档内容未变化，已复用现有索引')
      } else if (res.data?.replaced_existing) {
        ElMessage.success('文档已更新，旧索引已安全替换')
      } else {
        ElMessage.success('文档上传成功，已构建知识库')
      }
      load()
    } else {
      ElMessage.error(res.msg)
    }
  } catch (e) {
    ElMessage.error('上传失败')
  } finally {
    data.uploading = false
  }
}

const handleUpload = async (options) => {
  const extension = options.file.name.split('.').pop().toLowerCase()
  if (extension !== 'pdf') {
    await uploadDocument(options)
    return
  }

  data.uploading = true
  const formData = new FormData()
  formData.append('file', options.file)
  try {
    const res = await request.post('/knowledge/preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    if (res.code !== '200') {
      ElMessage.error(res.msg || 'PDF 预览失败')
      return
    }
    data.previewData = res.data || {}
    data.pendingUpload = options
    data.previewDialog = true
  } catch (e) {
    ElMessage.error('PDF 预览失败')
  } finally {
    data.uploading = false
  }
}

const confirmPreview = async () => {
  const options = data.pendingUpload
  if (!options) return
  data.previewDialog = false
  data.pendingUpload = null
  await uploadDocument(options)
}

const cancelPreview = () => {
  data.previewDialog = false
  data.pendingUpload = null
  data.previewData = {}
}

const handleDelete = (id) => {
  ElMessageBox.confirm('删除后知识库中将移除该文档的所有内容，确定删除吗？', '删除确认', { type: 'warning' }).then(() => {
    request.delete('/knowledge/delete/' + id).then(res => {
      if (res.code === '200') {
        ElMessage.success('删除成功')
        load()
      } else {
        ElMessage.error(res.msg)
      }
    })
  }).catch(() => {})
}

const formatSize = (bytes) => {
  if (!bytes) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

load()
</script>

<style lang="scss" scoped>
.knowledge-page {
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

.knowledge-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-radius: 5px;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.06);
  overflow: hidden;
}

.toolbar {
  min-height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 5px 16px;
  border-bottom: 1px solid #f0f2f5;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.format-hint {
  color: #9097a5;
  font-size: 12px;
}

.preview-warning {
  margin-top: 10px;
}

.detected-titles {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 12px 0;
}

.preview-label {
  margin: 12px 0 6px;
  color: #47556d;
  font-size: 13px;
  font-weight: 600;
}

.markdown-preview {
  max-height: 360px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background: #f8f9fb;
  color: #38455b;
  font: 12px/1.6 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.preview-truncated {
  margin-top: 6px;
  color: #9097a5;
  font-size: 12px;
}

.table-area {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
}

.table-area :deep(.el-table) {
  --el-table-border-color: #eef0f4;
  --el-table-header-bg-color: #f5f7fa;
  --el-table-row-hover-bg-color: #f0f5ff;
  font-size: 13px;
  border-radius: 0;
}

.table-area :deep(.el-table th.el-table__cell) {
  padding: 8px 0;
  font-size: 13px;
  font-weight: 600;
  color: #38455b;
  background-color: #f5f7fa;
}

.table-area :deep(.el-table td.el-table__cell) {
  padding: 6px 0;
  color: #47556d;
}

.table-area :deep(.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell) {
  background: #fafbfd;
}

.file-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.file-icon.icon-pdf {
  color: #e45656;
}

.file-icon.icon-docx {
  color: #1a73e8;
}

.file-icon.icon-txt {
  color: #9097a5;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
}

.empty-icon {
  font-size: 48px;
  color: #d6dce6;
  margin-bottom: 12px;
}

.empty-title {
  font-size: 15px;
  font-weight: 600;
  color: #7b8494;
  margin-bottom: 4px;
}

.empty-desc {
  font-size: 13px;
  color: #b0b7c3;
}

.action-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.action-group .action-btn {
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 500;
  border-width: 1px;
  border-style: solid;
  transition: all 0.2s ease;
}

.action-group .action-btn .el-icon {
  margin-right: 2px;
  font-size: 12px;
}

.action-group .action-btn--delete {
  color: #e45656;
  border-color: #f0c4c4;
  background-color: #fef2f2;
}

.action-group .action-btn--delete:hover {
  color: #ffffff;
  background-color: #e45656;
  border-color: #e45656;
  box-shadow: 0 2px 6px rgba(228, 86, 86, 0.3);
}
</style>
