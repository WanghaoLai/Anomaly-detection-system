<template>
  <div class="admin-page">
    <div class="page-header">
      <div class="page-icon"><el-icon><Key /></el-icon></div>
      <div>
        <div class="page-title">管理员信息</div>
        <div class="page-subtitle">共 {{ data.total }} 个管理员</div>
      </div>
    </div>

    <div class="admin-card">
      <div class="toolbar">
        <el-input
          v-model="data.name"
          style="width: 260px"
          placeholder="请输入名称查询"
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
          empty-text="暂无管理员信息"
        >
          <el-table-column label="用户名" prop="username" min-width="200" />
          <el-table-column label="名称" prop="name" min-width="200" />
          <el-table-column label="头像" width="100" align="center">
            <template #default="scope">
              <div class="avatar-cell">
                <el-image
                  v-if="scope.row.avatar"
                  preview-teleported
                  :src="scope.row.avatar"
                  :preview-src-list="[scope.row.avatar]"
                  class="avatar-img"
                >
                  <template #error>
                    <div class="avatar-fallback"><el-icon><User /></el-icon></div>
                  </template>
                </el-image>
                <div v-else class="avatar-fallback"><el-icon><User /></el-icon></div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="角色" prop="role" width="110" align="center">
            <template #default="scope">
              <el-tag :type="scope.row.role === '管理员' ? '' : 'success'" effect="light" round>
                <el-icon style="margin-right: 3px"><Key v-if="scope.row.role === '管理员'" /><User v-else /></el-icon>
                {{ scope.row.role }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" align="center" width="160" fixed="right">
            <template #default="scope">
              <div class="action-group">
                <el-button
                  class="action-btn action-btn--edit"
                  size="small"
                  round
                  @click="handleEdit(scope.row)"
                >
                  <el-icon><Edit /></el-icon>编辑
                </el-button>
                <el-button
                  class="action-btn action-btn--delete"
                  size="small"
                  round
                  @click="handleDelete(scope.row.id)"
                >
                  <el-icon><Delete /></el-icon>删除
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

    <el-dialog title="管理员信息" width="40%" v-model="data.formVisible" :close-on-click-modal="false" destroy-on-close>
      <el-form ref="formRef" :model="data.form" :rules="data.rules" label-width="100px" style="padding-right: 50px">
        <el-form-item label="账号" prop="username">
          <el-input :disabled="data.form.id > 0" v-model="data.form.username" autocomplete="off" />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="data.form.name" autocomplete="off" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="data.form.password"
            show-password
            autocomplete="new-password"
            :placeholder="data.form.id ? '不填则不修改密码' : '请输入密码'"
          />
        </el-form-item>
        <el-form-item label="头像" prop="avatar">
          <el-upload :action="uploadUrl" :headers="uploadHeaders" list-type="picture" :on-success="handleImgSuccess">
            <el-button type="primary">上传图片</el-button>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="data.formVisible = false">取 消</el-button>
          <el-button type="primary" @click="save">保 存</el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, ref, computed } from "vue"
import { Key, Plus, Edit, Delete } from "@element-plus/icons-vue"
import request from "@/utils/request"
import { ElMessageBox, ElMessage } from "element-plus"

const uploadUrl = import.meta.env.VITE_BASE_URL + '/files/upload'
const uploadHeaders = { Authorization: `Bearer ${localStorage.getItem('token')}` }

const formRef = ref()
const validatePassword = (rule, value, callback) => {
  if (!data.form.id && !value) {
    callback(new Error('请输入密码'))
  } else {
    callback()
  }
}

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  pageNum: 1,
  pageSize: 8,
  total: 0,
  formVisible: false,
  form: {},
  tableData: [],
  name: null,
  rules: {
    username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
    name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
    avatar: [{ required: true, message: '请上传头像', trigger: 'blur' }],
    password: [{ validator: validatePassword, trigger: 'blur' }],
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
  request.get('/admin/selectPage', {
    params: { pageNum: data.pageNum, pageSize: data.pageSize, name: data.name }
  }).then(res => {
    data.tableData = res.data?.list
    data.total = res.data?.total
  })
}
load()

const handleAdd = () => {
  data.form = {}
  data.formVisible = true
}

const handleEdit = (row) => {
  data.form = JSON.parse(JSON.stringify(row))
  delete data.form.password
  data.formVisible = true
}

const add = () => {
  request.post('/admin/add', data.form).then(res => {
    if (res.code === '200') {
      load()
      ElMessage.success('操作成功')
      data.formVisible = false
    } else {
      ElMessage.error(res.msg)
    }
  })
}

const update = () => {
  request.put('/admin/update', data.form).then(res => {
    if (res.code === '200') {
      load()
      ElMessage.success('操作成功')
      data.formVisible = false
    } else {
      ElMessage.error(res.msg)
    }
  })
}

const save = () => {
  formRef.value.validate(valid => {
    if (valid) {
      data.form.id ? update() : add()
    }
  })
}

const handleDelete = (id) => {
  ElMessageBox.confirm('删除后数据无法恢复，您确定删除吗?', '删除确认', { type: 'warning' }).then(() => {
    request.delete('/admin/delete/' + id).then(res => {
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

const handleImgSuccess = (res) => {
  data.form.avatar = res.data
}
</script>

<style lang="scss" scoped>
.admin-page {
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

.admin-card {
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

.avatar-cell {
  display: flex;
  align-items: center;
  justify-content: center;
}

.avatar-cell .avatar-img {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  border: 2px solid #eaf3ff;
  box-shadow: 0 2px 8px rgba(26, 115, 232, 0.15);
  cursor: pointer;
  transition: all 0.25s ease;
}

.avatar-cell .avatar-img:hover {
  transform: scale(1.15);
  box-shadow: 0 4px 12px rgba(26, 115, 232, 0.3);
  border-color: #1a73e8;
}

.avatar-fallback {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #eaf3ff 0%, #d6e6ff 100%);
  color: #1a73e8;
  font-size: 18px;
  border: 2px solid #d6e6ff;
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
