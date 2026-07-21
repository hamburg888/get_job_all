import { createRouter, createWebHistory } from 'vue-router'

import AccountsPage from '../pages/AccountsPage.vue'
import PlatformPage from '../pages/PlatformPage.vue'
import ResumeSyncPage from '../pages/ResumeSyncPage.vue'
import SettingsPage from '../pages/SettingsPage.vue'
import AppShell from '../layouts/AppShell.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: AppShell,
      children: [
        { path: '', redirect: '/platform/zhilian/config' },
        {
          path: 'platform/:platform(zhilian|qiancheng|liepin|boss|yupao)/:section(config|analysis|live)',
          name: 'platform',
          component: PlatformPage,
        },
        {
          path: 'accounts',
          name: 'accounts',
          component: AccountsPage,
        },
        {
          path: 'resume-sync',
          name: 'resume-sync',
          component: ResumeSyncPage,
        },
        {
          path: 'settings',
          name: 'settings',
          component: SettingsPage,
        },
      ],
    },
  ],
})

export default router
