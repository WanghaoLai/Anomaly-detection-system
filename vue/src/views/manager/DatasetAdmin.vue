<template>
  <div class="dataset-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><Coin /></el-icon></div>
      <div>
        <div class="page-title">数据集管理</div>
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
          <el-button type="primary" @click="handleAdd"><el-icon><Plus /></el-icon>新增</el-button>
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
          <el-table-column label="名称" prop="name" width="100" show-overflow-tooltip />
          <el-table-column label="描述" prop="description" min-width="140" show-overflow-tooltip />
          <el-table-column label="领域类型" prop="domain_type" width="100" align="center" />
          <el-table-column label="数据源目录" prop="root_directory" width="120" align="center" show-overflow-tooltip />
          <el-table-column label="类别数" prop="class_count" width="80" align="center" />
          <el-table-column label="训练样本" prop="train_sample_count" width="90" align="center" />
          <el-table-column label="测试样本" prop="test_sample_count" width="90" align="center" />
          <el-table-column label="异常样本" prop="anomaly_sample_count" width="90" align="center" />
          <el-table-column label="操作" align="center" width="160" fixed="right">
            <template #default="scope">
              <div class="action-group">
                <el-button class="action-btn action-btn--edit" size="small" round @click="handleEdit(scope.row)">
                  <el-icon><Edit /></el-icon>
                </el-button>
                <el-button class="action-btn action-btn--delete" size="small" round @click="handleDelete(scope.row.id)">
                  <el-icon><Delete /></el-icon>
                </el-button>
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
          @current-change="load"
        />
      </div>
    </div>

    <el-dialog title="数据集信息" width="50%" v-model="data.formVisible" :close-on-click-modal="false" destroy-on-close>
      <el-form ref="formRef" :model="data.form" :rules="data.rules" label-width="100px" scroll-to-error style="padding-right: 50px">
        <div class="form-section-title">基本信息</div>
        <el-form-item label="数据集编号">
          <el-input
            :model-value="data.form.id ? data.form.dataset_no : '保存后由系统自动生成'"
            disabled
          />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="data.form.name" autocomplete="off" placeholder="请输入数据集名称" />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input type="textarea" :rows="3" v-model="data.form.description" autocomplete="off" placeholder="请输入数据集描述" />
        </el-form-item>
        <el-form-item label="领域类型" prop="domain_type">
          <el-input v-model="data.form.domain_type" autocomplete="off" placeholder="如：工业、医学、交通等" />
        </el-form-item>
        <el-divider />
        <div class="form-section-title">统计信息</div>
        <el-form-item label="数据源目录">
          <el-input v-model="data.form.root_directory" autocomplete="off" placeholder="请输入数据源目录路径" />
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="类别数量">
              <el-input-number v-model="data.form.class_count" :min="0" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="训练样本">
              <el-input-number v-model="data.form.train_sample_count" :min="0" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="测试样本">
              <el-input-number v-model="data.form.test_sample_count" :min="0" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="异常样本">
              <el-input-number v-model="data.form.anomaly_sample_count" :min="0" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button :disabled="data.saving" @click="data.formVisible = false">取 消</el-button>
          <el-button type="primary" :loading="data.saving" @click="save">保 存</el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, ref, computed } from "vue"
import { Coin, Plus, Edit, Delete } from "@element-plus/icons-vue"
import request from "@/utils/request"
import { ElMessage, ElMessageBox } from "element-plus"

const formRef = ref()

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  form: {},
  formVisible: false,
  saving: false,
  name: null,
  pageNum: 1,
  pageSize: 8,
  total: 0,
  tableData: [],
  rules: {
    name: [{ required: true, message: '请输入数据集名称', trigger: 'blur' }],
  }
})

const PAGE_HEADER_H = 54
const TOOLBAR_H = 52
const PAGINATION_H = 44
const PAGE_PADDING = 10
const TABLE_HEADER_H = 36
const TABLE_BODY_H = computed(() => Math.min(data.tableData.length, data.pageSize) * 44)

const tableHeight = computed(() =>
  PAGE_HEADER_H + TOOLBAR_H + PAGE_PADDING + TABLE_HEADER_H + TABLE_BODY_H.value + PAGINATION_H + 2
)

const load = () => {
  request.get('/dataset/selectPage', {
    params: { pageNum: data.pageNum, pageSize: data.pageSize, name: data.name, userId: 0 }
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

const handleAdd = () => {
  data.form = { class_count: 0, train_sample_count: 0, test_sample_count: 0, anomaly_sample_count: 0 }
  data.formVisible = true
}

const handleEdit = (row) => {
  data.form = JSON.parse(JSON.stringify(row))
  data.formVisible = true
}

const ensureSuccess = (res, fallback) => {
  if (res.code !== '200') throw new Error(res.msg || fallback)
  return res
}

const add = async () => {
  const datasetData = {
    name: data.form.name,
    description: data.form.description,
    domain_type: data.form.domain_type,
    createdBy: data.user.id,
  }
  const res = ensureSuccess(
    await request.post('/dataset/add', datasetData),
    '数据集基本信息保存失败',
  )
  const infoData = {
    datasetId: res.data,
    root_directory: data.form.root_directory,
    class_count: data.form.class_count,
    train_sample_count: data.form.train_sample_count,
    test_sample_count: data.form.test_sample_count,
    anomaly_sample_count: data.form.anomaly_sample_count,
  }
  ensureSuccess(
    await request.post('/dataset/info/add', infoData),
    '数据集统计信息保存失败',
  )
}

const update = async () => {
  const datasetData = {
    id: data.form.id,
    name: data.form.name,
    description: data.form.description,
    domain_type: data.form.domain_type,
  }
  ensureSuccess(
    await request.put('/dataset/update', datasetData),
    '数据集基本信息更新失败',
  )
  const infoData = {
    id: data.form.info_id,
    root_directory: data.form.root_directory,
    class_count: data.form.class_count,
    train_sample_count: data.form.train_sample_count,
    test_sample_count: data.form.test_sample_count,
    anomaly_sample_count: data.form.anomaly_sample_count,
  }
  if (data.form.info_id) {
    ensureSuccess(
      await request.put('/dataset/info/update', infoData),
      '数据集统计信息更新失败',
    )
  } else {
    delete infoData.id
    infoData.datasetId = data.form.id
    ensureSuccess(
      await request.post('/dataset/info/add', infoData),
      '数据集统计信息保存失败',
    )
  }
}

const save = async () => {
  if (data.saving) return
  try {
    await formRef.value.validate()
  } catch {
    ElMessage.warning('请填写数据集名称')
    return
  }

  data.saving = true
  try {
    if (data.form.id) await update()
    else await add()
    ElMessage.success('保存成功')
    data.formVisible = false
    load()
  } catch (error) {
    ElMessage.error(error?.message || '保存失败，请稍后重试')
  } finally {
    data.saving = false
  }
}

const handleDelete = (id) => {
  ElMessageBox.confirm('删除后数据无法恢复，您确定删除吗?', '删除确认', { type: 'warning' }).then(() => {
    request.delete('/dataset/delete/' + id).then(res => {
      if (res.code === '200') {
        load()
        ElMessage.success('操作成功')
      } else {
        ElMessage.error(res.msg)
      }
    })
  }).catch(() => {})
}

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

.table-area :deep(.el-table .el-table__empty-block) {
  min-height: 160px;
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

.action-group .action-btn--edit {
  color: #1a73e8;
  border-color: #c6d8f2;
  background-color: #eaf3ff;
}

.action-group .action-btn--edit:hover {
  color: #ffffff;
  background-color: #1a73e8;
  border-color: #1a73e8;
  box-shadow: 0 2px 6px rgba(26, 115, 232, 0.3);
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

.pagination-bar {
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 3px 16px 8px;
  border-top: 1px solid #f0f2f5;
}

.form-section-title {
  font-size: 14px;
  font-weight: 600;
  color: #263247;
  margin-bottom: 16px;
  padding-left: 8px;
  border-left: 3px solid #1a73e8;
}

:deep(.el-divider) {
  margin: 16px 0;
}
</style>
