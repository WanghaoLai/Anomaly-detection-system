<template>
  <div class="algo-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><SetUp /></el-icon></div>
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
          <el-button type="primary" @click="handleAdd"><el-icon><Plus /></el-icon>新增</el-button>
        </div>
      </div>

      <div class="table-area">
        <el-table
          :data="data.tableData"
          stripe
          :max-height="tableHeight"
          empty-text="暂无算法信息"
        >
          <el-table-column label="编号" prop="algorithm_no" width="70" align="center" />
          <el-table-column label="名称" prop="name" width="80" show-overflow-tooltip />
          <el-table-column label="简称" prop="abbreviation" width="70" align="center" show-overflow-tooltip />
          <el-table-column label="描述" prop="description" min-width="100" show-overflow-tooltip />
          <el-table-column label="任务类别" prop="task_category" width="90" align="center" show-overflow-tooltip />
          <el-table-column label="框架" width="110" align="center">
            <template #default="scope">
              {{ scope.row.framework || '--' }}{{ scope.row.framework_version ? ` ${scope.row.framework_version}` : '' }}
            </template>
          </el-table-column>
          <el-table-column label="Conda 环境" width="120" align="center" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.conda_env_name || '--' }}</template>
          </el-table-column>
          <el-table-column label="执行配置" width="125" align="center">
            <template #default="scope">
              {{ scope.row.executor_type || '--' }} / {{ scope.row.process_manager || '--' }}
            </template>
          </el-table-column>
          <el-table-column label="创建者" prop="created_by_name" width="80" align="center" />
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

    <el-dialog title="算法信息" width="55%" v-model="data.formVisible" :close-on-click-modal="false" destroy-on-close>
      <el-form ref="formRef" :model="data.form" :rules="data.rules" label-width="110px" scroll-to-error style="padding-right: 50px">
        <div class="form-section-title">基本信息</div>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="算法编号">
              <el-input
                :model-value="data.form.id ? data.form.algorithm_no : '保存后由系统自动生成'"
                disabled
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="名称" prop="name">
              <el-input v-model="data.form.name" placeholder="请输入算法名称" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="简称">
              <el-input v-model="data.form.abbreviation" placeholder="请输入算法简称" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="任务类别">
              <el-input v-model="data.form.task_category" placeholder="如：ANOMALY_DETECTION" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="描述">
          <el-input type="textarea" :rows="3" v-model="data.form.description" placeholder="请输入算法描述" />
        </el-form-item>

        <el-divider />
        <div class="form-section-title">环境信息</div>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="框架" prop="framework">
              <el-input v-model="data.form.framework" placeholder="如：PyTorch" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="框架版本">
              <el-input v-model="data.form.framework_version" placeholder="如：2.1.0" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="Python 版本">
              <el-input v-model="data.form.python_version" placeholder="如：3.10" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="CUDA 要求">
              <el-input v-model="data.form.cuda_requirement" placeholder="如：11.8" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="Conda 环境" prop="conda_env_name">
              <el-input v-model="data.form.conda_env_name" placeholder="如：pbas-prod" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="Conda 路径">
              <el-input v-model="data.form.conda_env_path" placeholder="如：/opt/conda/envs/pbas-prod" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="工作目录" prop="working_directory">
          <el-input v-model="data.form.working_directory" placeholder="算法代码所在的服务器绝对路径" />
        </el-form-item>

        <el-divider />
        <div class="form-section-title">运行信息</div>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="训练入口" prop="train_entrypoint">
              <el-input v-model="data.form.train_entrypoint" placeholder="训练入口脚本路径" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="推理入口">
              <el-input v-model="data.form.inference_entrypoint" placeholder="推理入口脚本路径" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="执行器">
              <el-select v-model="data.form.executor_type" style="width: 100%">
                <el-option label="GPU 训练执行器" value="GPU" />
                <el-option label="CPU 执行器" value="CPU" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="进程管理">
              <el-select v-model="data.form.process_manager" style="width: 100%">
                <el-option label="systemd 任务" value="SYSTEMD" />
                <el-option label="独立进程组" value="PROCESS_GROUP" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="JSONL 协议">
              <el-input v-model="data.form.protocol_version" placeholder="如：1.0" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="SSE 推送">
              <el-switch
                v-model="data.form.sse_enabled"
                inline-prompt
                active-text="启用"
                inactive-text="停用"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider />
        <div class="form-section-title">结构定义</div>
        <el-form-item label="参数结构">
          <el-input type="textarea" :rows="3" v-model="data.form.parameter_schema_json" placeholder='JSON 格式的参数结构定义' />
        </el-form-item>
        <el-form-item label="输出结构">
          <el-input type="textarea" :rows="3" v-model="data.form.output_schema_json" placeholder='JSON 格式的输出结构定义' />
        </el-form-item>
        <el-form-item label="资源需求">
          <el-input type="textarea" :rows="3" v-model="data.form.resource_spec_json" placeholder='JSON 格式的资源需求规格' />
        </el-form-item>
        <el-form-item label="数据集要求">
          <el-input type="textarea" :rows="3" v-model="data.form.dataset_requirement_json" placeholder='JSON 格式的数据集要求定义' />
        </el-form-item>
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
import { SetUp, Plus, Edit, Delete } from "@element-plus/icons-vue"
import request from "@/utils/request"
import { ElMessage, ElMessageBox } from "element-plus"

const formRef = ref()

const JSON_FIELDS = ['parameter_schema_json', 'output_schema_json', 'resource_spec_json', 'dataset_requirement_json']

const parseJsonFields = (obj) => {
  const result = { ...obj }
  for (const key of JSON_FIELDS) {
    const val = result[key]
    if (val == null || val === '') {
      result[key] = null
    } else if (typeof val === 'string') {
      try {
        result[key] = JSON.parse(val)
      } catch {
        ElMessage.warning(`字段 "${key}" 的内容不是有效 JSON，已忽略`)
        result[key] = null
      }
    }
  }
  return result
}

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
    name: [{ required: true, message: '请输入算法名称', trigger: 'blur' }],
    framework: [{ required: true, message: '请输入算法框架', trigger: 'blur' }],
    conda_env_name: [{ required: true, message: '请输入 Conda 环境名称', trigger: 'blur' }],
    working_directory: [{ required: true, message: '请输入算法工作目录', trigger: 'blur' }],
    train_entrypoint: [{ required: true, message: '请输入训练入口脚本', trigger: 'blur' }],
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
  request.get('/algorithm/selectPage', {
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
  data.form = {
    task_category: 'ANOMALY_DETECTION',
    executor_type: 'GPU',
    process_manager: 'SYSTEMD',
    protocol_version: '1.0',
    sse_enabled: true,
  }
  data.formVisible = true
}

const handleEdit = (row) => {
  data.form = JSON.parse(JSON.stringify(row))
  for (const key of JSON_FIELDS) {
    const val = data.form[key]
    if (val != null && typeof val === 'object') {
      data.form[key] = JSON.stringify(val, null, 2)
    }
  }
  data.formVisible = true
}

const ensureSuccess = (res, fallback) => {
  if (res.code !== '200') throw new Error(res.msg || fallback)
  return res
}

const buildInfoData = () => parseJsonFields({
  framework: data.form.framework,
  framework_version: data.form.framework_version,
  python_version: data.form.python_version,
  cuda_requirement: data.form.cuda_requirement,
  conda_env_name: data.form.conda_env_name,
  conda_env_path: data.form.conda_env_path,
  working_directory: data.form.working_directory,
  train_entrypoint: data.form.train_entrypoint,
  inference_entrypoint: data.form.inference_entrypoint,
  executor_type: data.form.executor_type,
  process_manager: data.form.process_manager,
  protocol_version: data.form.protocol_version,
  sse_enabled: data.form.sse_enabled,
  parameter_schema_json: data.form.parameter_schema_json,
  output_schema_json: data.form.output_schema_json,
  resource_spec_json: data.form.resource_spec_json,
  dataset_requirement_json: data.form.dataset_requirement_json,
})

const add = async () => {
  const algoData = {
    name: data.form.name,
    abbreviation: data.form.abbreviation,
    description: data.form.description,
    task_category: data.form.task_category,
    createdBy: data.user.id,
  }
  const res = ensureSuccess(
    await request.post('/algorithm/add', algoData),
    '算法基本信息保存失败',
  )
  const infoData = buildInfoData()
  infoData.algorithmId = res.data
  ensureSuccess(
    await request.post('/algorithm/info/add', infoData),
    '算法运行信息保存失败',
  )
}

const update = async () => {
  const algoData = {
    id: data.form.id,
    name: data.form.name,
    abbreviation: data.form.abbreviation,
    description: data.form.description,
    task_category: data.form.task_category,
  }
  ensureSuccess(
    await request.put('/algorithm/update', algoData),
    '算法基本信息更新失败',
  )
  const infoData = buildInfoData()
  if (data.form.info_id) {
    infoData.id = data.form.info_id
    ensureSuccess(
      await request.put('/algorithm/info/update', infoData),
      '算法运行信息更新失败',
    )
  } else {
    infoData.algorithmId = data.form.id
    ensureSuccess(
      await request.post('/algorithm/info/add', infoData),
      '算法运行信息保存失败',
    )
  }
}

const save = async () => {
  if (data.saving) return
  try {
    await formRef.value.validate()
  } catch {
    ElMessage.warning('请完善所有必填的算法信息')
    return
  }

  for (const key of JSON_FIELDS) {
    const value = data.form[key]
    if (typeof value === 'string' && value.trim()) {
      try {
        JSON.parse(value)
      } catch {
        ElMessage.error(`字段 "${key}" 必须是有效的 JSON`)
        return
      }
    }
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
    request.delete('/algorithm/delete/' + id).then(res => {
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
