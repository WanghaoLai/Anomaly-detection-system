import { ElMessage } from 'element-plus'

// el-upload 的 on-error 统一回调：解析后端 {code, msg} 错误响应并提示；
// 网络错误等非 JSON 响应回退为通用文案。
export const handleUploadError = (error) => {
  let msg = '上传失败，请检查文件格式与大小'
  try {
    msg = JSON.parse(error?.message).msg || msg
  } catch { /* 非 JSON 响应使用默认提示 */ }
  ElMessage.error(msg)
}
