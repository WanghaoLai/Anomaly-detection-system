<template>
  <div class="dataset-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><Coin /></el-icon></div>
      <div>
        <div class="page-title">数据集信息</div>
        <div class="page-subtitle">共 {{ data.total }} 个数据集</div>
      </div>
    </div>

    <div class="dataset-card">
      <div class="toolbar">
        <el-input
          v-model="data.name"
          style="width: 260px"
          placeholder="请输入数据集名称查询"
          clearable
          @keyup.enter="load"
        />
        <div class="toolbar-actions">
          <el-button type="primary" @click="load">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </div>
      </div>

      <div class="table-area">
        <el-table
          :data="data.tableData"
          stripe
          :max-height="tableHeight"
          empty-text="暂无数据集信息"
        >
          <el-table-column label="编号" prop="dataset_no" width="70" align="center" />
          <el-table-column label="名称" prop="name" min-width="170" show-overflow-tooltip />
          <el-table-column label="描述" prop="description" min-width="220" show-overflow-tooltip />
          <el-table-column label="领域类型" prop="domain_type" width="120" align="center" />
          <el-table-column label="类别数量" prop="class_count" width="100" align="center" />
          <el-table-column label="训练 / 测试样本" width="150" align="center">
            <template #default="scope">
              {{ scope.row.train_sample_count ?? 0 }} / {{ scope.row.test_sample_count ?? 0 }}
            </template>
          </el-table-column>
          <el-table-column label="创建者" prop="created_by_name" width="100" align="center" />
          <el-table-column label="详情" width="90" align="center" fixed="right">
            <template #default="scope">
              <el-button type="primary" link @click="showDetail(scope.row)">查看</el-button>
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
          @current-change="load"
        />
      </div>
    </div>

    <el-dialog v-model="data.detailVisible" title="数据集详细信息" width="720px" align="center">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="数据集编号">{{ detailValue('dataset_no') }}</el-descriptions-item>
        <el-descriptions-item label="数据集名称">{{ detailValue('name') }}</el-descriptions-item>
        <el-descriptions-item label="领域类型">{{ detailValue('domain_type') }}</el-descriptions-item>
        <el-descriptions-item label="类别数量">{{ countValue('class_count') }}</el-descriptions-item>
        <el-descriptions-item label="数据集描述" :span="2">{{ detailValue('description') }}</el-descriptions-item>
        <el-descriptions-item label="数据源目录" :span="2">{{ detailValue('root_directory') }}</el-descriptions-item>
        <el-descriptions-item label="训练样本">{{ countValue('train_sample_count') }}</el-descriptions-item>
        <el-descriptions-item label="测试样本">{{ countValue('test_sample_count') }}</el-descriptions-item>
        <el-descriptions-item label="异常样本">{{ countValue('anomaly_sample_count') }}</el-descriptions-item>
        <el-descriptions-item label="创建账号">{{ detailValue('created_by_name') }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatCompactDate(data.detailRow.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="更新时间" :span="2">{{ formatCompactDate(data.detailRow.updated_at) }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, computed } from "vue"
import { Coin } from "@element-plus/icons-vue"
import request from "@/utils/request"
import { ElMessage } from "element-plus"

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  name: null,
  pageNum: 1,
  pageSize: 8,
  total: 0,
  tableData: [],
  detailVisible: false,
  detailRow: {},
})

const PAGE_HEADER_H = 54
const TOOLBAR_H = 52
const PAGINATION_H = 44
const PAGE_PADDING = 10
const TABLE_HEADER_H = 36
const TABLE_BODY_H = computed(() => Math.min(data.tableData.length, data.pageSize) * 36)

const tableHeight = computed(() =>
  PAGE_HEADER_H + TOOLBAR_H + PAGE_PADDING + TABLE_HEADER_H + TABLE_BODY_H.value + PAGINATION_H + 2
)

const detailValue = (key) => data.detailRow[key] ?? '--'
const countValue = (key) => data.detailRow[key] ?? 0

const showDetail = (row) => {
  data.detailRow = { ...row }
  data.detailVisible = true
}

const formatCompactDate = (value) => {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '--'
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  const h = String(date.getHours()).padStart(2, '0')
  const min = String(date.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${d} ${h}:${min}`
}

const load = () => {
  request.get('/dataset/selectPage', {
    params: {
      pageNum: data.pageNum,
      pageSize: data.pageSize,
      name: data.name,
      userId: 0
    }
  }).then(res => {
    if (res.code === '200') {
      data.tableData = res.data?.list
      data.total = res.data?.total
    } else {
      ElMessage.error(res.msg)
    }
  })
}
load()

const reset = () => {
  data.name = null
  load()
}
</script>

<style lang="scss" scoped>
.dataset-page {
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

.dataset-card {
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

.toolbar-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.table-area {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.table-area :deep(.el-table) {
  --el-table-border-color: #eef0f4;
  --el-table-header-bg-color: #f5f7fa;
  --el-table-row-hover-bg-color: #f0f5ff;
  font-size: 13px;
  border-radius: 0;
}

.table-area :deep(.el-table__body-wrapper) {
  overflow-x: auto;
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

:deep(.el-dialog__body) {
  max-height: 68vh;
  overflow-y: auto;
}

.pagination-bar {
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 3px 16px 8px;
  border-top: 1px solid #f0f2f5;
}
</style>
