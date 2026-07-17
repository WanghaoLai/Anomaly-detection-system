<template>
  <div>
    <div class="card" style="margin-bottom: 5px;">
      <el-input v-model="data.name" style="width: 300px; margin-right: 10px" placeholder="请输入算法名称查询"></el-input>
      <el-button type="primary" @click="load">查询</el-button>
      <el-button type="info" style="margin: 0 10px" @click="reset">重置</el-button>
    </div>
    <div class="card" style="margin-bottom: 5px">
      <el-table :data="data.tableData" stripe>
        <el-table-column label="编号" prop="algorithm_no" width="80" show-overflow-tooltip></el-table-column>
        <el-table-column label="名称" prop="name" width="100" align="center" show-overflow-tooltip></el-table-column>
        <el-table-column label="简称" prop="abbreviation" width="100" align="center" show-overflow-tooltip></el-table-column>
        <el-table-column label="描述" prop="description" align="center" show-overflow-tooltip></el-table-column>
        <el-table-column label="任务类别" prop="task_category" width="100" align="center" show-overflow-tooltip></el-table-column>
        <el-table-column label="框架" prop="framework" width="100" align="center"></el-table-column>
        <el-table-column label="框架版本" prop="framework_version" width="100" align="center"></el-table-column>
        <el-table-column label="Python版本" prop="python_version" width="100" align="center"></el-table-column>
        <el-table-column label="CUDA版本" prop="cuda_requirement" width="100" align="center"></el-table-column>
        <el-table-column label="训练脚本" prop="train_entrypoint" width="120" align="center" show-overflow-tooltip></el-table-column>
        <el-table-column label="推理脚本" prop="inference_entrypoint" width="120" align="center" show-overflow-tooltip></el-table-column>
        <el-table-column label="镜像地址" prop="docker_image" width="140" align="center" show-overflow-tooltip></el-table-column>
        <el-table-column label="镜像摘要" prop="docker_image_digest" width="140" align="center" show-overflow-tooltip></el-table-column>
        <el-table-column label="参数结构" prop="parameter_schema_json" width="100" align="center" show-overflow-tooltip>
          <template #default="scope">
            <span>{{ formatJson(scope.row.parameter_schema_json) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="输出结构" prop="output_schema_json" width="100" align="center" show-overflow-tooltip>
          <template #default="scope">
            <span>{{ formatJson(scope.row.output_schema_json) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="资源需求" prop="resource_spec_json" width="100" align="center" show-overflow-tooltip>
          <template #default="scope">
            <span>{{ formatJson(scope.row.resource_spec_json) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="数据集要求" prop="dataset_requirement_json" width="100" align="center" show-overflow-tooltip>
          <template #default="scope">
            <span>{{ formatJson(scope.row.dataset_requirement_json) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建者" prop="created_by_name" width="100" align="center"></el-table-column>
        <el-table-column label="创建时间" prop="created_at" width="160" align="center"></el-table-column>
        <el-table-column label="更新时间" prop="updated_at" width="160" align="center"></el-table-column>
      </el-table>
    </div>
    <div class="card">
      <el-pagination @current-change="load" background layout="total, prev, pager, next" v-model:page-size="data.pageSize" v-model:current-page="data.pageNum" :total="data.total"/>
    </div>
  </div>
</template>

<script setup>
import { reactive } from "vue";
import request from "@/utils/request";
import { ElMessage } from "element-plus";

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  name: null,
  pageNum: 1,
  pageSize: 8,
  total: 0,
  tableData: [],
})

const formatJson = (val) => {
  if (!val) return '-'
  return typeof val === 'string' ? val : JSON.stringify(val)
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
