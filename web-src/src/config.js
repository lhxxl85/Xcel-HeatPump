export const APP_CONFIG = {
  auth: {
    username: 'admin',
    password: 'admin'
  },
  apiBaseUrl: process.env.VUE_APP_API_BASE_URL || '',
  // 连续多少次请求 comm_status 不变化后判定离线
  commStatusStaleThreshold: 10,
  heatpumpIds: [1, 3, 4, 5, 6, 7],
  ctIds: [1]
}
