<template>
  <div class="shell">
    <section v-if="page === 'login'" class="panel auth-panel">
      <h1>{{ t('title') }}</h1>
      <p class="subtitle">{{ t('signInHint') }}</p>

      <form class="auth-form" @submit.prevent="handleLogin">
        <label>
          {{ t('username') }}
          <input v-model.trim="form.username" type="text" autocomplete="username" />
        </label>
        <label>
          {{ t('password') }}
          <input v-model.trim="form.password" type="password" autocomplete="current-password" />
        </label>
        <button type="submit">{{ t('signIn') }}</button>
      </form>
    </section>

    <section v-else-if="page === 'unauthorized'" class="panel unauthorized-panel">
      <h2>{{ t('unauthorized') }}</h2>
      <p>{{ t('badCredential') }}</p>
      <button @click="backToLogin">{{ t('backToLogin') }}</button>
    </section>

    <section v-else class="dashboard">
      <header class="toolbar">
        <div>
          <h2>{{ t('deviceStatus') }}</h2>
          <p class="subtitle">{{ t('liveDataHint') }}</p>
        </div>
        <div class="toolbar-actions">
          <button class="ghost" @click="toggleLang">{{ languageToggleLabel }}</button>
          <button class="ghost" @click="logout">{{ t('logout') }}</button>
        </div>
      </header>

      <nav class="tabs">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="['tab', { active: activeTab === tab.key }]"
          @click="selectTab(tab.key)"
        >
          {{ tab.label }}
        </button>
      </nav>

      <div class="panel status-panel">
        <div class="status-head">
          <span class="badge">{{ currentTabLabel }}</span>
          <button class="ghost refresh-btn" :disabled="loading" @click="loadStatus">
            <span class="refresh-content" :class="{ hidden: loading }">{{ t('refresh') }}</span>
            <span v-if="loading" class="spinner refresh-spinner" aria-label="loading" />
          </button>
        </div>

        <div class="comm-banner" :class="commOnline ? 'online' : 'offline'">
          <strong>{{ t('commStatus') }}:</strong>
          <span>{{ commOnline ? t('online') : t('offline') }}</span>
          <small>comm_status={{ currentCommStatus }}</small>
        </div>

        <p v-if="errorMessage" class="state-text error">{{ errorMessage }}</p>

        <div v-if="isHeatpumpTab" class="hp-layout">
          <aside class="hp-sidebar">
            <button
              v-for="section in hpSections"
              :key="section.key"
              :class="['hp-nav-btn', { active: heatpumpSection === section.key }]"
              @click="heatpumpSection = section.key"
            >
              {{ section.label }}
            </button>
          </aside>

          <section class="hp-content">
            <div v-if="activeBitPanels.length > 0" class="bit-panels compact">
              <section v-for="panel in activeBitPanels" :key="`bit-${panel.address}`" class="bit-panel">
                <header class="bit-panel-head">
                  <strong>{{ panel.name }}</strong>
                  <small>{{ formatAddress(panel.address) }} | raw={{ panel.rawValue }}</small>
                </header>

                <div class="bit-list compact">
                  <div v-for="bit in panel.bits" :key="`${panel.address}-${bit.bit}`" class="bit-item compact">
                    <span class="bit-label">b{{ bit.bit }} · {{ bit.label }}</span>

                    <template v-if="panel.mode === 'writable'">
                      <button
                        class="switch"
                        :class="bit.on ? 'on' : 'off'"
                        :disabled="!commOnline || writeLocked"
                        @click="toggleBit(panel, bit)"
                      >
                        {{ bit.on ? 'ON' : 'OFF' }}
                      </button>
                    </template>

                    <template v-else-if="panel.mode === 'status'">
                      <span class="status-pill" :class="bit.on ? 'on' : 'off'">{{ bit.on ? 'ON' : 'OFF' }}</span>
                    </template>

                    <template v-else>
                      <span class="alarm-dot" :class="bit.on ? 'alarm-on' : 'alarm-off'" />
                    </template>
                  </div>
                </div>
              </section>
            </div>

            <div v-if="activeNormalItems.length > 0" class="status-grid">
              <template v-for="item in activeNormalItems" :key="`${item['display-order']}-${item.name}`">
                <div class="cell key-cell">
                  <div class="key-title">{{ displayName(item.name) }}</div>
                  <div class="key-meta">
                    addr: {{ formatAddress(item.address) }} | order: {{ item['display-order'] }}
                  </div>
                </div>

                <div class="cell value-cell">
                  <template v-if="isWritable(item)">
                    <input
                      :value="inputValue(item)"
                      class="value-input"
                      :disabled="writeLocked"
                      @input="setInputValue(item, $event.target.value)"
                      @keyup.enter="submitCommand(item)"
                    />
                    <button class="mini" :disabled="writeLocked || !commOnline" @click="submitCommand(item)">
                      {{ t('write') }}
                    </button>
                  </template>
                  <template v-else>
                    <span class="value-text">{{ renderValue(item.value) }}</span>
                  </template>
                </div>
              </template>
            </div>

            <p v-if="activeBitPanels.length === 0 && activeNormalItems.length === 0 && !loading" class="state-text">
              {{ t('noData') }}
            </p>
          </section>
        </div>

        <template v-else>
          <div v-if="statusItems.length > 0" class="status-grid">
            <template v-for="item in statusItems" :key="`${item['display-order']}-${item.name}`">
              <div class="cell key-cell">
                <div class="key-title">{{ displayName(item.name) }}</div>
                <div class="key-meta">
                  addr: {{ formatAddress(item.address) }} | order: {{ item['display-order'] }}
                </div>
              </div>

              <div class="cell value-cell">
                <span class="value-text">{{ renderValue(item.value) }}</span>
              </div>
            </template>
          </div>
          <p v-else-if="!loading" class="state-text">{{ t('noData') }}</p>
        </template>
      </div>
    </section>
  </div>
</template>

<script>
import { APP_CONFIG } from './config'
import { buildBitRegisterPanels, isBitRegisterAddress, setBitValue } from './bit-registers'
import { displayRegisterName } from './register-labels'
const STATUS_REQUEST_TIMEOUT_MS = 3000
const STATUS_REFRESH_INTERVAL_MS = 500

const I18N = {
  en: {
    title: 'HeatPump Console',
    signInHint: 'Please sign in to continue.',
    username: 'Username',
    password: 'Password',
    signIn: 'Sign In',
    unauthorized: 'Unauthorized',
    badCredential: 'Invalid username or password.',
    backToLogin: 'Back to Login',
    deviceStatus: 'Device Status',
    liveDataHint: 'Live data from API endpoints',
    logout: 'Logout',
    refresh: 'Refresh',
    commStatus: 'Communication',
    online: 'online',
    offline: 'offline',
    write: 'Write',
    noData: 'No data',
    status: 'Status',
    control: 'Control',
    output: 'Output',
    alarm: 'Alarm'
  },
  zh: {
    title: '热泵控制台',
    signInHint: '请输入账号密码登录。',
    username: '用户名',
    password: '密码',
    signIn: '登录',
    unauthorized: '未授权',
    badCredential: '用户名或密码错误。',
    backToLogin: '返回登录',
    deviceStatus: '设备状态',
    liveDataHint: '来自 API 的实时数据',
    logout: '退出登录',
    refresh: '刷新',
    commStatus: '通讯状态',
    online: '在线',
    offline: '离线',
    write: '写入',
    noData: '暂无数据',
    status: '状态',
    control: '控制',
    output: '输出',
    alarm: '报警'
  }
}

export default {
  name: 'App',
  data() {
    return {
      page: 'login',
      lang: 'en',
      form: {
        username: '',
        password: ''
      },
      tabs: [],
      activeTab: '',
      heatpumpSection: 'status',
      statusItems: [],
      loading: false,
      errorMessage: '',
      editableValues: {},
      commOnline: false,
      currentCommStatus: -1,
      commStatusMemory: {},
      commStatusUnchangedCount: {},
      statusCache: {},
      refreshTimer: null,
      statusRequestInFlight: false,
      writeLocked: false,
      writeLockTabKey: '',
      writeLockBaseline: null
    }
  },
  computed: {
    currentTab() {
      return this.tabs.find((t) => t.key === this.activeTab) || null
    },
    isHeatpumpTab() {
      return !!this.currentTab && this.currentTab.type === 'heatpump'
    },
    currentTabLabel() {
      return this.currentTab ? this.currentTab.label : '--'
    },
    apiBaseUrl() {
      return APP_CONFIG.apiBaseUrl
    },
    languageToggleLabel() {
      return this.lang === 'zh' ? 'EN' : '中文'
    },
    hpSections() {
      return [
        { key: 'status', label: this.t('status') },
        { key: 'control', label: this.t('control') },
        { key: 'output', label: this.t('output') },
        { key: 'alarm', label: this.t('alarm') }
      ]
    },
    bitPanels() {
      if (!this.isHeatpumpTab) {
        return []
      }
      return buildBitRegisterPanels(this.statusItems, this.lang)
    },
    activeBitPanels() {
      return this.bitPanels.filter((panel) => panel.section === this.heatpumpSection)
    },
    normalStatusItems() {
      return this.statusItems.filter((item) => !isBitRegisterAddress(item.address))
    },
    activeNormalItems() {
      if (!this.isHeatpumpTab) {
        return this.statusItems
      }
      if (this.heatpumpSection === 'control') {
        return this.normalStatusItems.filter((item) => item['read-only'] === false)
      }
      if (this.heatpumpSection === 'status') {
        return this.normalStatusItems.filter((item) => item['read-only'] === true)
      }
      return []
    }
  },
  beforeUnmount() {
    this.stopAutoRefresh()
  },
  methods: {
    t(key) {
      return (I18N[this.lang] && I18N[this.lang][key]) || key
    },
    toggleLang() {
      this.lang = this.lang === 'en' ? 'zh' : 'en'
      this.bootstrapTabs()
      if (this.page === 'dashboard') {
        this.loadStatus()
      }
    },
    handleLogin() {
      if (
        this.form.username === APP_CONFIG.auth.username
        && this.form.password === APP_CONFIG.auth.password
      ) {
        this.page = 'dashboard'
        this.bootstrapTabs()
        this.loadStatus()
        this.startAutoRefresh()
        return
      }
      this.page = 'unauthorized'
    },
    backToLogin() {
      this.form.password = ''
      this.page = 'login'
    },
    logout() {
      this.stopAutoRefresh()
      this.page = 'login'
      this.form = { username: '', password: '' }
      this.statusItems = []
      this.errorMessage = ''
      this.editableValues = {}
      this.commOnline = false
      this.currentCommStatus = -1
      this.commStatusMemory = {}
      this.commStatusUnchangedCount = {}
      this.statusCache = {}
      this.writeLocked = false
      this.writeLockTabKey = ''
      this.writeLockBaseline = null
    },
    bootstrapTabs() {
      const hpPrefix = this.lang === 'zh' ? '热泵' : 'HeatPump'
      const ctPrefix = this.lang === 'zh' ? '互感器' : 'CT'
      const hpTabs = APP_CONFIG.heatpumpIds.map((id) => ({
        key: `hp-${id}`,
        type: 'heatpump',
        id,
        label: `${hpPrefix} ${id}`
      }))
      const ctTabs = APP_CONFIG.ctIds.map((id) => ({
        key: `ct-${id}`,
        type: 'ct',
        id,
        label: `${ctPrefix} ${id}`
      }))
      this.tabs = [...hpTabs, ...ctTabs]
      if (!this.activeTab && this.tabs.length > 0) {
        this.activeTab = this.tabs[0].key
      }
    },
    selectTab(key) {
      if (this.activeTab === key) {
        return
      }
      this.activeTab = key
      this.heatpumpSection = 'status'
      this.statusItems = []
      this.errorMessage = ''
      this.editableValues = {}
      this.commOnline = false
      this.currentCommStatus = -1
      if (this.statusCache[key]) {
        this.statusItems = this.statusCache[key].items || []
        this.currentCommStatus = this.statusCache[key].commStatus ?? -1
        this.commOnline = this.statusCache[key].commOnline === true
      }
      this.loadStatus()
    },
    startAutoRefresh() {
      this.stopAutoRefresh()
      const tick = async () => {
        if (this.page !== 'dashboard') {
          this.stopAutoRefresh()
          return
        }
        await this.loadStatus()
        if (this.page !== 'dashboard') {
          this.stopAutoRefresh()
          return
        }
        this.refreshTimer = setTimeout(tick, STATUS_REFRESH_INTERVAL_MS)
      }
      this.refreshTimer = setTimeout(tick, STATUS_REFRESH_INTERVAL_MS)
    },
    stopAutoRefresh() {
      if (this.refreshTimer) {
        clearTimeout(this.refreshTimer)
        this.refreshTimer = null
      }
    },
    evaluateCommStatus(tabKey, commStatus) {
      const threshold = Number(APP_CONFIG.commStatusStaleThreshold) || 10
      const prev = Object.prototype.hasOwnProperty.call(this.commStatusMemory, tabKey)
        ? this.commStatusMemory[tabKey]
        : null
      this.currentCommStatus = commStatus

      if (commStatus === -1) {
        this.commStatusMemory[tabKey] = commStatus
        this.commStatusUnchangedCount[tabKey] = 0
        this.commOnline = false
        return
      }

      if (prev !== null && prev === commStatus) {
        const count = (this.commStatusUnchangedCount[tabKey] || 0) + 1
        this.commStatusUnchangedCount[tabKey] = count
        this.commOnline = count < threshold
      } else {
        this.commStatusUnchangedCount[tabKey] = 0
        this.commOnline = true
      }
      this.commStatusMemory[tabKey] = commStatus

      if (
        this.writeLocked
        && this.writeLockTabKey === tabKey
        && commStatus !== -1
        && commStatus !== this.writeLockBaseline
      ) {
        this.writeLocked = false
        this.writeLockTabKey = ''
        this.writeLockBaseline = null
      }
    },
    async loadStatus() {
      if (!this.currentTab) {
        return
      }
      if (this.statusRequestInFlight) {
        return
      }
      this.statusRequestInFlight = true
      this.loading = true
      this.errorMessage = ''
      let timeoutHandle = null
      const controller = new AbortController()
      try {
        const url = `${this.apiBaseUrl}/api/v1/${this.currentTab.type}/${this.currentTab.id}/status?lang=${this.lang}`
        timeoutHandle = setTimeout(() => controller.abort(), STATUS_REQUEST_TIMEOUT_MS)
        const response = await fetch(url, { signal: controller.signal })
        const payload = await response.json()
        if (!response.ok || !payload.success) {
          this.errorMessage = payload.message || 'Request failed'
          if (this.statusItems.length === 0) {
            this.commOnline = false
            this.currentCommStatus = -1
          }
          return
        }

        const data = payload.data || {}
        let items = []
        let commStatus = -1

        if (Array.isArray(data.items)) {
          items = data.items
          commStatus = Number.isFinite(Number(data.comm_status)) ? Number(data.comm_status) : -1
        } else if (Array.isArray(data)) {
          items = data
          const commItem = data.find((x) => x && x.name === 'comm_status')
          if (commItem && Number.isFinite(Number(commItem.value))) {
            commStatus = Number(commItem.value)
          }
        }

        this.evaluateCommStatus(this.currentTab.key, commStatus)
        this.statusCache[this.currentTab.key] = {
          items,
          commStatus,
          commOnline: this.commOnline
        }
        this.statusItems = items
      } catch (error) {
        if (error && error.name === 'AbortError') {
          this.errorMessage = `Request timeout after ${STATUS_REQUEST_TIMEOUT_MS / 1000}s`
        } else {
          this.errorMessage = `Network error: ${error}`
        }
        if (this.statusItems.length === 0) {
          this.commOnline = false
          this.currentCommStatus = -1
        }
      } finally {
        if (timeoutHandle) {
          clearTimeout(timeoutHandle)
        }
        this.statusRequestInFlight = false
        this.loading = false
      }
    },
    isWritable(item) {
      return this.isHeatpumpTab && item['read-only'] === false
    },
    inputValue(item) {
      const key = String(item.address)
      if (Object.prototype.hasOwnProperty.call(this.editableValues, key)) {
        return this.editableValues[key]
      }
      return String(item.value)
    },
    setInputValue(item, value) {
      this.editableValues[String(item.address)] = value
    },
    async submitCommand(item) {
      if (!this.isWritable(item) || !this.commOnline || this.writeLocked) {
        return
      }
      const address = Number(item.address)
      const rawValue = this.inputValue(item)
      const value = Number(rawValue)

      if (!Number.isInteger(address) || !Number.isFinite(value)) {
        this.errorMessage = `Invalid command value for ${item.name}`
        return
      }

      try {
        const url = `${this.apiBaseUrl}/api/v1/heatpump/${this.currentTab.id}/cmd`
        const response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ address, value })
        })
        const payload = await response.json()
        if (!response.ok || !payload.success) {
          this.errorMessage = payload.message || 'Command rejected'
          return
        }
        this.errorMessage = ''
        this.writeLocked = true
        this.writeLockTabKey = this.currentTab.key
        this.writeLockBaseline = this.currentCommStatus
        this.editableValues = {}
        await this.loadStatus()
      } catch (error) {
        this.errorMessage = `Network error: ${error}`
      }
    },
    async toggleBit(panel, bit) {
      if (!this.isHeatpumpTab || !this.commOnline || panel.mode !== 'writable' || this.writeLocked) {
        return
      }

      const nextValue = setBitValue(panel.rawValue, bit.bit, !bit.on)
      try {
        const url = `${this.apiBaseUrl}/api/v1/heatpump/${this.currentTab.id}/cmd`
        const response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ address: panel.address, value: nextValue })
        })
        const payload = await response.json()
        if (!response.ok || !payload.success) {
          this.errorMessage = payload.message || `Command rejected for ${panel.name}`
          return
        }
        this.errorMessage = ''
        this.writeLocked = true
        this.writeLockTabKey = this.currentTab.key
        this.writeLockBaseline = this.currentCommStatus
        await this.loadStatus()
      } catch (error) {
        this.errorMessage = `Network error: ${error}`
      }
    },
    formatAddress(address) {
      if (typeof address !== 'number' || address < 0) {
        return 'N/A'
      }
      return `0x${address.toString(16).toUpperCase().padStart(4, '0')}`
    },
    renderValue(value) {
      if (value === null || value === undefined || value === '') {
        return 'N/A'
      }
      if (typeof value === 'object') {
        return JSON.stringify(value)
      }
      return String(value)
    },
    displayName(rawName) {
      return displayRegisterName(rawName, this.lang)
    }
  }
}
</script>

<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {
  --bg: radial-gradient(circle at 10% 20%, #d2f0ff 0%, #f7fbff 40%, #f0ffe8 100%);
  --ink: #102232;
  --subtle: #4f6575;
  --line: #c4d4df;
  --panel: rgba(255, 255, 255, 0.92);
  --brand: #0a6b74;
  --brand-strong: #074d54;
  --warn: #8f2e2a;
  --ok-bg: #ddf7e7;
  --ok-fg: #1a6a3a;
  --bad-bg: #fde4e4;
  --bad-fg: #a42f2f;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: 'Manrope', sans-serif;
  color: var(--ink);
  background: var(--bg);
}

.shell { min-height: 100vh; padding: 24px; }

.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 16px 34px rgba(0, 0, 0, 0.08);
}

.auth-panel,
.unauthorized-panel {
  width: min(460px, 92vw);
  margin: 10vh auto 0;
  padding: 28px;
}

h1, h2 { margin: 0 0 10px; }

.subtitle { margin: 0; color: var(--subtle); }

.auth-form { margin-top: 20px; display: grid; gap: 12px; }
.auth-form label { display: grid; gap: 6px; font-weight: 600; }

input, button { font: inherit; }

input {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px 12px;
}

button {
  border: 0;
  border-radius: 10px;
  padding: 10px 14px;
  background: var(--brand);
  color: #fff;
  font-weight: 700;
  cursor: pointer;
}
button:hover { background: var(--brand-strong); }

.dashboard { max-width: 1400px; margin: 0 auto; }

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}

.toolbar-actions { display: flex; gap: 8px; }

.ghost {
  background: transparent;
  border: 1px solid var(--line);
  color: var(--ink);
}
.ghost:disabled { opacity: 0.7; cursor: wait; }

.refresh-btn {
  width: 96px;
  height: 36px;
  display: inline-flex;
  justify-content: center;
  align-items: center;
  position: relative;
  padding: 0;
}

.refresh-content.hidden {
  opacity: 0;
}

.refresh-spinner {
  position: absolute;
}

.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid #9db5c3;
  border-top-color: #0a6b74;
  border-radius: 50%;
  animation: spin 0.75s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.tab {
  background: #fff;
  color: var(--ink);
  border: 1px solid var(--line);
}
.tab.active {
  background: var(--brand);
  color: #fff;
  border-color: var(--brand);
}

.status-panel { padding: 14px; }

.status-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.badge {
  display: inline-flex;
  align-items: center;
  background: #e8f7f8;
  color: #065059;
  border: 1px solid #b8e6ea;
  border-radius: 999px;
  padding: 6px 10px;
  font-weight: 700;
}

.comm-banner {
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 10px;
  display: flex;
  gap: 10px;
  align-items: baseline;
  border: 1px solid transparent;
}
.comm-banner.online {
  background: var(--ok-bg);
  color: var(--ok-fg);
  border-color: #a7e5c0;
}
.comm-banner.offline {
  background: var(--bad-bg);
  color: var(--bad-fg);
  border-color: #f3b9b9;
}

.state-text { margin: 12px 0; color: var(--subtle); }
.state-text.error { color: var(--warn); }

.hp-layout {
  display: grid;
  grid-template-columns: 180px 1fr;
  gap: 12px;
}

.hp-sidebar {
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  padding: 8px;
  display: grid;
  align-content: start;
  gap: 6px;
}

.hp-nav-btn {
  text-align: left;
  background: #fff;
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
}

.hp-nav-btn.active {
  background: var(--brand);
  color: #fff;
  border-color: var(--brand);
}

.bit-panels { display: grid; gap: 8px; margin-bottom: 10px; }
.bit-panel {
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  padding: 6px;
}
.bit-panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 4px;
}
.bit-panel-head small {
  color: var(--subtle);
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
}

.bit-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}
.bit-list.compact { grid-template-columns: repeat(4, minmax(0, 1fr)); }

.bit-item {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fdfefe;
  padding: 6px 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.bit-item.compact { padding: 4px 6px; min-height: 34px; }

.bit-label {
  font-size: 11px;
  color: var(--ink);
  word-break: break-all;
}

.switch {
  min-width: 48px;
  border-radius: 999px;
  padding: 3px 8px;
  border: 0;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
}
.switch.on { background: #1d8f4a; }
.switch.off { background: #7d8f9b; }

.status-pill {
  min-width: 48px;
  text-align: center;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
}
.status-pill.on { background: #1d8f4a; }
.status-pill.off { background: #7d8f9b; }

.alarm-dot {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 1px solid #8da2b0;
}
.alarm-dot.alarm-on { background: #d33f3f; border-color: #c12222; }
.alarm-dot.alarm-off { background: #2fb368; border-color: #17924d; }

.status-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 6px;
}

.cell {
  border: 1px solid var(--line);
  background: #fff;
  border-radius: 8px;
  min-height: 48px;
  display: flex;
  align-items: center;
  padding: 8px;
}

.key-cell {
  grid-column: span 2;
  display: grid;
  align-content: center;
  gap: 2px;
}
.value-cell {
  grid-column: span 2;
  justify-content: space-between;
  gap: 6px;
}

.key-title {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  font-weight: 600;
}
.key-meta { font-size: 11px; color: var(--subtle); }

.value-text {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 13px;
  word-break: break-all;
}

.value-input {
  width: 100%;
  min-width: 0;
  padding: 6px 8px;
}
.mini { padding: 6px 8px; font-size: 12px; }

@media (max-width: 1100px) {
  .hp-layout { grid-template-columns: 1fr; }
  .hp-sidebar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    display: grid;
  }
  .bit-list.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 980px) {
  .status-grid { grid-template-columns: repeat(8, minmax(0, 1fr)); }
}

@media (max-width: 680px) {
  .shell { padding: 14px; }
  .status-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .bit-list,
  .bit-list.compact { grid-template-columns: repeat(1, minmax(0, 1fr)); }
}
</style>
