<template>
  <div class="chat-page">
    <div class="chat-container">
      <div class="conversation-sidebar">
        <div class="sidebar-header">
          <el-button class="new-chat-btn" @click="createConversation">
            <el-icon><Plus /></el-icon>新对话
          </el-button>
        </div>
        <div class="conversation-list">
          <div
            v-for="conv in data.conversations"
            :key="conv.id"
            :class="['conversation-item', { active: data.currentConversation === conv.id }]"
            @click="switchConversation(conv.id)"
          >
            <div class="conv-icon"><el-icon><ChatDotRound /></el-icon></div>
            <div class="conv-info">
              <div class="conv-title">{{ conv.title }}</div>
              <div class="conv-time">{{ formatTime(conv.updated_at) }}</div>
            </div>
            <el-button
              class="delete-btn"
              :icon="Delete"
              circle
              size="small"
              @click.stop="deleteConversation(conv.id)"
            />
          </div>
          <div v-if="data.conversations.length === 0" class="sidebar-empty">
            <el-icon :size="28" color="#d6dce6"><ChatDotRound /></el-icon>
            <span>暂无对话</span>
          </div>
        </div>
      </div>

      <div class="chat-main">
        <div class="messages-container" ref="messagesContainer">
          <div v-if="data.messages.length === 0" class="empty-state">
            <div class="empty-icon"><el-icon :size="40"><ChatDotRound /></el-icon></div>
            <div class="empty-title">智能助手</div>
            <div class="empty-desc">输入您的问题，我将为您提供帮助</div>
          </div>

          <div v-for="(msg, index) in data.messages" :key="index" :class="['message', msg.role]">
            <div class="message-avatar">
              <img v-if="msg.role === 'user'" :src="data.user.avatar || 'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png'" class="avatar-img" />
              <div v-else class="avatar-ai"><el-icon><Monitor /></el-icon></div>
            </div>
            <div class="message-bubble">
              <div
                class="message-text"
                v-html="renderMarkdown(msg.content)"
                @click="handleCitationClick($event, msg)"
              ></div>
              <div v-if="msg.role === 'assistant' && msg.sources && msg.sources.length" class="source-bar">
                <div class="source-bar-title">
                  <el-icon :size="12"><Collection /></el-icon>
                  <span>参考来源</span>
                </div>
                <div class="source-cards">
                  <div
                    v-for="source in msg.sources"
                    :key="source.citation_id"
                    :class="['source-card', { active: msg.activeSource === source.citation_id }]"
                    :title="source.snippet"
                  >
                    <span class="source-chip">{{ source.citation_id }}</span>
                    <span class="source-name">{{ source.source }}</span>
                    <span v-if="source.heading || source.position" class="source-loc">
                      {{ [source.heading, source.position].filter(Boolean).join(' · ') }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-if="data.loading" class="message assistant">
            <div class="message-avatar">
              <div class="avatar-ai"><el-icon><Monitor /></el-icon></div>
            </div>
            <div class="message-bubble">
              <div class="message-text loading">
                <span class="dot"></span>
                <span class="dot"></span>
                <span class="dot"></span>
              </div>
            </div>
          </div>
        </div>

        <div class="input-container">
          <el-input
            v-model="data.inputMessage"
            type="textarea"
            :rows="2"
            placeholder="输入您的问题... (Enter 发送，Ctrl+Enter 换行)"
            @keydown.enter.ctrl="sendMessage"
            @keydown.enter.exact.prevent="sendMessage"
          />
          <el-button class="send-btn" type="primary" :disabled="!data.inputMessage.trim() || data.loading" @click="sendMessage">
            <el-icon><Promotion /></el-icon>发送
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, nextTick, onMounted } from 'vue'
import { Plus, Delete, ChatDotRound, Collection, Monitor, Promotion } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
import { API_BASE_URL, getCsrfToken } from '@/utils/auth'
import { renderMarkdown } from '@/utils/markdown'

const messagesContainer = ref(null)

const data = reactive({
  user: JSON.parse(localStorage.getItem('system-user') || '{}'),
  conversations: [],
  currentConversation: null,
  messages: [],
  inputMessage: '',
  loading: false
})

const formatTime = (value) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()
  const h = String(date.getHours()).padStart(2, '0')
  const min = String(date.getMinutes()).padStart(2, '0')
  if (isToday) return `${h}:${min}`
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${m}-${d} ${h}:${min}`
}

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

const handleCitationClick = (event, msg) => {
  const chip = event.target.closest('.citation-chip')
  if (!chip || !msg.sources?.length) return
  const citationId = chip.textContent.trim()
  msg.activeSource = msg.activeSource === citationId ? null : citationId
}

const loadConversations = async () => {
  try {
    const res = await request.get('/chat/conversations')
    if (res.code === '200') {
      data.conversations = res.data || []
    }
  } catch (error) {
    console.error('加载会话列表失败:', error)
  }
}

const createConversation = async () => {
  try {
    const res = await request.post('/chat/conversation', { title: '新对话' })
    if (res.code === '200') {
      await loadConversations()
      data.currentConversation = res.data.id
      data.messages = []
    }
  } catch (error) {
    ElMessage.error('创建会话失败')
  }
}

const switchConversation = async (conversationId) => {
  data.currentConversation = conversationId
  try {
    const res = await request.get(`/chat/messages/${conversationId}`)
    if (res.code === '200') {
      data.messages = res.data || []
      scrollToBottom()
    }
  } catch (error) {
    ElMessage.error('加载消息失败')
  }
}

const deleteConversation = async (conversationId) => {
  try {
    await ElMessageBox.confirm('确定删除这个对话吗？', '确认删除', { type: 'warning' })
    const res = await request.delete(`/chat/conversation/${conversationId}`)
    if (res.code === '200') {
      ElMessage.success('删除成功')
      if (data.currentConversation === conversationId) {
        data.currentConversation = null
        data.messages = []
      }
      await loadConversations()
    }
  } catch (error) {
    if (error !== 'cancel') ElMessage.error('删除失败')
  }
}

const sendMessage = async () => {
  if (!data.inputMessage.trim() || data.loading) return
  if (!data.currentConversation) await createConversation()

  const userMessage = data.inputMessage.trim()
  data.inputMessage = ''
  data.messages.push({ role: 'user', content: userMessage })
  scrollToBottom()

  data.loading = true
  data.messages.push({ role: 'assistant', content: '' })

  try {
    const response = await fetch(`${API_BASE_URL}/chat/send`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken()
      },
      body: JSON.stringify({ conversation_id: data.currentConversation, message: userMessage })
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.msg || '发送消息失败')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let assistantMessage = ''
    let sseBuffer = ''
    let terminalReceived = false
    let streamFailure = null

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      sseBuffer += decoder.decode(value, { stream: true })
      const frames = sseBuffer.split('\n\n')
      sseBuffer = frames.pop() || ''
      for (const frame of frames) {
        const dataLines = frame.split('\n')
          .filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart())
        if (!dataLines.length) continue
        try {
          const sseData = JSON.parse(dataLines.join('\n'))
          if (sseData.content) {
            assistantMessage += sseData.content
            const lastMsg = data.messages[data.messages.length - 1]
            if (lastMsg?.role === 'assistant') lastMsg.content = assistantMessage
            scrollToBottom()
          }
          if (sseData.sources) {
            const lastMsg = data.messages[data.messages.length - 1]
            if (lastMsg?.role === 'assistant') {
              lastMsg.sources = sseData.sources
              lastMsg.activeSource = null
            }
          }
          if (sseData.status === 'failed') {
            streamFailure = sseData.message || '模型生成失败，请稍后重试。'
          }
          if (sseData.done) {
            terminalReceived = true
            break
          }
        } catch (e) {
          console.error('解析 SSE 数据失败:', e)
        }
      }
      if (terminalReceived) break
    }
    if (streamFailure) throw new Error(streamFailure)
    if (!terminalReceived) throw new Error('连接已断开，回答未完成。')
    await loadConversations()
  } catch (error) {
    ElMessage.error(error?.message || '发送消息失败')
  } finally {
    data.loading = false
    scrollToBottom()
  }
}

onMounted(() => loadConversations())
</script>

<style lang="scss" scoped>
.chat-page {
  height: calc(100vh - 80px);
  overflow: hidden;
}

.chat-container {
  display: flex;
  height: 100%;
  background: #ffffff;
  border-radius: 5px;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.06);
  overflow: hidden;
}

.conversation-sidebar {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #f7f9fc;
  border-right: 1px solid #eef0f4;
}

.sidebar-header {
  padding: 12px;
  border-bottom: 1px solid #eef0f4;
}

.new-chat-btn {
  width: 100%;
  background: linear-gradient(135deg, #1a73e8 0%, #00c6ff 100%);
  border: none;
  color: #ffffff;
  font-weight: 600;
  border-radius: 6px;
  height: 38px;
  transition: box-shadow 0.2s ease;
}

.new-chat-btn:hover {
  box-shadow: 0 3px 10px rgba(26, 115, 232, 0.35);
}

.conversation-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.conversation-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
  position: relative;
  transition: all 0.2s ease;
  border: 1px solid transparent;
}

.conversation-item:hover {
  background: #eef3fb;
}

.conversation-item.active {
  background: #eaf3ff;
  border-color: #c6d8f2;
}

.conv-icon {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: #eaf3ff;
  color: #1a73e8;
  font-size: 15px;
}

.conversation-item.active .conv-icon {
  background: #1a73e8;
  color: #ffffff;
}

.conv-info {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 13px;
  color: #263247;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-right: 24px;
}

.conv-time {
  font-size: 11px;
  color: #9097a5;
  margin-top: 2px;
}

.delete-btn {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  opacity: 0;
  transition: opacity 0.2s;
}

.conversation-item:hover .delete-btn {
  opacity: 1;
}

.sidebar-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 40px 16px;
  color: #b0b7c3;
  font-size: 13px;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}

.empty-icon {
  width: 72px;
  height: 72px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #eaf3ff;
  color: #1a73e8;
  margin-bottom: 16px;
}

.empty-title {
  font-size: 18px;
  font-weight: 700;
  color: #263247;
  margin-bottom: 6px;
}

.empty-desc {
  font-size: 13px;
  color: #9097a5;
}

.message {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.message.user {
  flex-direction: row-reverse;
}

.message-avatar {
  flex-shrink: 0;
}

.avatar-img {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 2px solid #eaf3ff;
  object-fit: cover;
}

.avatar-ai {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #1a73e8 0%, #00c6ff 100%);
  color: #ffffff;
  font-size: 18px;
}

.message-bubble {
  max-width: 70%;
}

.message-text {
  padding: 12px 16px;
  border-radius: 12px;
  line-height: 1.7;
  word-break: break-word;
  font-size: 14px;
}

.message.user .message-text {
  background: linear-gradient(135deg, #1a73e8 0%, #2b8af7 100%);
  color: #ffffff;
  border-top-right-radius: 4px;
}

.message.assistant .message-text {
  background: #f5f7fa;
  color: #263247;
  border-top-left-radius: 4px;
  border: 1px solid #eef0f4;
}

.source-bar {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #e4e8ef;
}

.source-bar-title {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #9097a5;
  margin-bottom: 6px;
}

.source-cards {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.source-card {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 4px 10px;
  background: #ffffff;
  border: 1px solid #e4e8ef;
  border-radius: 6px;
  font-size: 12px;
  color: #5a6474;
  cursor: default;
  transition: all 0.2s ease;
}

.source-card.active {
  border-color: #1a73e8;
  background: #eaf3ff;
  color: #1a73e8;
}

.source-chip {
  flex-shrink: 0;
  padding: 1px 5px;
  border-radius: 4px;
  background: #eaf3ff;
  color: #1a73e8;
  font-size: 11px;
  font-weight: 600;
}

.source-card.active .source-chip {
  background: #1a73e8;
  color: #ffffff;
}

.source-name {
  font-weight: 500;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-loc {
  color: #9097a5;
  font-size: 11px;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-card.active .source-loc {
  color: #5a8fd6;
}

.message-text.loading {
  display: flex;
  gap: 5px;
  align-items: center;
  padding: 14px 20px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #1a73e8;
  animation: bounce 1.4s infinite ease-in-out both;
}

.dot:nth-child(2) { animation-delay: 0.16s; }
.dot:nth-child(3) { animation-delay: 0.32s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.3); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

.input-container {
  padding: 12px 16px;
  border-top: 1px solid #eef0f4;
  display: flex;
  gap: 10px;
  align-items: flex-end;
  background: #fafbfc;
}

.input-container :deep(.el-textarea__inner) {
  resize: none;
  border-radius: 8px;
  padding: 10px 14px;
}

.send-btn {
  height: 42px;
  min-width: 80px;
  border-radius: 8px;
  background: linear-gradient(135deg, #1a73e8 0%, #00c6ff 100%);
  border: none;
  font-weight: 600;
}

.send-btn:disabled {
  background: #c0c4cc;
}
</style>

<style lang="scss">
.message-text p { margin: 0; }
.message-text p + p { margin-top: 8px; }

.message-text .citation-chip {
  display: inline-block;
  margin: 0 2px;
  padding: 0 5px;
  border-radius: 4px;
  background: rgba(26, 115, 232, 0.1);
  color: #1a73e8;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.5;
  cursor: pointer;
  vertical-align: baseline;
  user-select: none;
}

.message-text .citation-chip:hover {
  background: rgba(26, 115, 232, 0.22);
}

.message-text pre {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}

.message-text code {
  background: rgba(26, 115, 232, 0.08);
  color: #1a73e8;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}

.message-text pre code {
  background: none;
  color: inherit;
  padding: 0;
}

.message-text ul, .message-text ol {
  padding-left: 20px;
  margin: 8px 0;
}

.message-text table {
  border-collapse: collapse;
  margin: 8px 0;
  width: 100%;
}

.message-text th, .message-text td {
  border: 1px solid #eef0f4;
  padding: 8px 12px;
  text-align: left;
}

.message-text th {
  background: #f5f7fa;
  font-weight: 600;
}
</style>
