<template>
  <div>
    <div class="card" style="margin-bottom: 5px;">
      <el-input v-model="data.name" style="width: 300px; margin-right: 10px" placeholder="请输入数据集名称查询"></el-input>
      <el-button type="primary" @click="load">查询</el-button>
      <el-button type="info" style="margin: 0 10px" @click="reset">重置</el-button>
    </div>
    <div class="card" style="margin-bottom: 5px">
      <el-table :data="data.tableData" stripe>
        <el-table-column label="编号" prop="dataset_no" width="80" show-overflow-tooltip></el-table-column>
        <el-table-column label="名称" prop="name" width="100" show-overflow-tooltip align="center"></el-table-column>
        <el-table-column label="描述" prop="description" show-overflow-tooltip align="center"></el-table-column>
        <el-table-column label="领域类型" prop="domain_type" width="120" align="center"></el-table-column>
        <el-table-column label="类别数量" prop="class_count" width="100" align="center"></el-table-column>
        <el-table-column label="训练样本数量" prop="train_sample_count" width="130" align="center"></el-table-column>
        <el-table-column label="测试样本数量" prop="test_sample_count" width="130" align="center"></el-table-column>
        <el-table-column label="异常样本数量" prop="anomaly_sample_count" width="130" align="center"></el-table-column>
        <el-table-column label="掩码数量" prop="mask_count" width="100" align="center"></el-table-column>
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

const load = () => {
  request.get('/dataset/selectPage', {
    params: {
      pageNum: data.pageNum,
      pageSize: data.pageSize,
      name: data.name,
      userId: data.user.role === '用户' ? data.user.id : 0
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
