<template>
  <div class="algo-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><Cpu /></el-icon></div>
      <div>
        <div class="page-title">算法管理</div>
        <div class="page-subtitle">共 {{ data.total }} 个算法</div>
      </div>
    </div>

    <div class="algo-card">
      <div class="toolbar">
        <el-input
          v-model="data.name"
          style="width: 260px"
          placeholder="请输入算法名称查询"
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
          empty-text="暂无算法信息"
        >
          <el-table-column label="编号" prop="algorithm_no" width="60" align="center" />
          <el-table-column label="名称" prop="name" width="80" show-overflow-tooltip />
          <el-table-column label="简称" prop="abbreviation" width="70" align="center" show-overflow-tooltip />
          <el-table-column label="描述" prop="description" min-width="100" show-overflow-tooltip />
          <el-table-column label="任务类别" prop="task_category" width="80" align="center" show-overflow-tooltip />
          <el-table-column label="框架" width="110" align="center">
            <template #default="scope">
              {{ scope.row.framework }}{{ scope.row.framework_version ? ` ${scope.row.framework_version}` : '' }}
            </template>
          </el-table-column>
          <el-table-column label="Python / CUDA" width="110" align="center">
            <template #default="scope">
              <div class="cell-stack">
                <span>{{ scope.row.python_version || '--' }}</span>
                <span class="cell-sub">{{ scope.row.cuda_requirement || '--' }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="入口脚本" width="120" align="center">
            <template #default="scope">
              <div class="cell-stack">
                <span :title="scope.row.train_entrypoint">{{ scope.row.train_entrypoint || '--' }}</span>
                <span class="cell-sub" :title="scope.row.inference_entrypoint">{{ scope.row.inference_entrypoint || '--' }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="Docker 镜像" width="110" align="center" show-overflow-tooltip>
            <template #default="scope">
              <div class="cell-stack">
                <span>{{ scope.row.docker_image || '--' }}</span>
                <span v-if="scope.row.docker_image_digest" class="cell-sub" :title="scope.row.docker_image_digest">{{ scope.row.docker_image_digest }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="参数结构" width="100" align="center" show-overflow-tooltip>
            <template #default="scope">{{ formatJson(scope.row.parameter_schema_json) }}</template>
          </el-table-column>
          <el-table-column label="输出结构" width="100" align="center" show-overflow-tooltip>
            <template #default="scope">{{ formatJson(scope.row.output_schema_json) }}</template>
          </el-table-column>
          <el-table-column label="资源需求" width="100" align="center" show-overflow-tooltip>
            <template #default="scope">{{ formatJson(scope.row.resource_spec_json) }}</template>
          </el-table-column>
          <el-table-column label="数据集要求" width="100" align="center" show-overflow-tooltip>
            <template #default="scope">{{ formatJson(scope.row.dataset_requirement_json) }}</template>
          </el-table-column>
          <el-table-column label="创建者" prop="created_by_name" width="70" align="center" />
          <el-table-column label="创建时间" width="100" align="center">
            <template #default="scope">{{ formatCompactDate(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="更新时间" width="100" align="center">
            <template #default="scope">{{ formatCompactDate(scope.row.updated_at) }}</template>
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
  </div>
</template>

<script setup>
import { reactive, computed } from "vue"
import { Cpu } from "@element-plus/icons-vue"
import request from "@/utils/request"
import { ElMessage } from "element-plus"

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  name: null,
  pageNum: 1,
  pageSize: 8,
  total: 0,
  tableData: [],
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

const formatJson = (val) => {
  if (!val) return '--'
  return typeof val === 'string' ? val : JSON.stringify(val)
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
  request.get('/algorithm/selectPage', {
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
.algo-page {
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

.algo-card {
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

.cell-stack {
  display: flex;
  flex-direction: column;
  gap: 1px;
  line-height: 1.4;
}

.cell-sub {
  color: #9aa1ad;
  font-size: 11px;
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
