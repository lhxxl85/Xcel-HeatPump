const { defineConfig } = require('@vue/cli-service')

module.exports = defineConfig({
  transpileDependencies: true,
  devServer: {
    proxy: {
      '^/api': {
        target: process.env.VUE_APP_API_PROXY_TARGET || 'http://127.0.0.1:8010',
        changeOrigin: true
      }
    }
  }
})
