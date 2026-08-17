import { marked } from 'marked'
import DOMPurify from 'dompurify'

// marked v5+ 已移除内置 sanitize 选项，原生 HTML 的过滤统一由 DOMPurify 白名单完成。
// gfm/breaks 显式固定为当前默认值，避免 marked 升级改变默认行为时聊天渲染跟着变化。
marked.use({ gfm: true, breaks: false })

// 白名单只保留 Markdown 常规语法会产出的标签；script、iframe、form、内联事件
// 处理器等不在名单内，会被整体移除。
const ALLOWED_TAGS = [
  'a', 'b', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4',
  'h5', 'h6', 'hr', 'i', 'img', 'input', 'kbd', 'li', 'mark', 'ol', 'p', 'pre',
  's', 'span', 'strong', 'sub', 'sup', 'table', 'tbody', 'td', 'th', 'thead',
  'tr', 'u', 'ul',
]

const ALLOWED_ATTR = [
  'alt', 'checked', 'disabled', 'href', 'src', 'title', 'type',
]

// 仅放行 http/https/mailto，拦截 javascript:、data:、vbscript: 等危险协议。
const ALLOWED_URI_REGEXP = /^(?:https?:|mailto:)/i

export const renderMarkdown = (text) =>
  DOMPurify.sanitize(marked(text || ''), {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOWED_URI_REGEXP,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  })
