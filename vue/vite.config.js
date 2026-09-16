import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
// 导入对应包

import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

import ElementPlus from 'unplugin-element-plus/vite'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://127.0.0.1:9090'

  return {
    plugins: [
      vue(),
      AutoImport({
        resolvers: [ElementPlusResolver(
            { importStyle: 'sass' }
        )],
      }),
      Components({
        resolvers: [ElementPlusResolver(
            { importStyle: 'sass' }
        )],
      }),

      // 按需定制主题配置
      ElementPlus({
        useSource: true,
      }),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    server: {
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
    css: {
      preprocessorOptions: {
        scss: {
          api: 'modern',
          // 自动导入定制化样式文件进行样式覆盖
          additionalData: `
            @use "@/assets/css/index.scss" as *;
          `,
        }
      }
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            framework: ['vue', 'vue-router', 'axios'],
            element: ['element-plus', '@element-plus/icons-vue'],
            markdown: ['marked', 'dompurify'],
          },
        },
      }
    }
  }
})
