import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import Affix from 'ant-design-vue/es/affix'
import Alert from 'ant-design-vue/es/alert'
import BackTop from 'ant-design-vue/es/float-button'
import Button from 'ant-design-vue/es/button'
import Card from 'ant-design-vue/es/card'
import Checkbox from 'ant-design-vue/es/checkbox'
import Col from 'ant-design-vue/es/col'
import Collapse from 'ant-design-vue/es/collapse'
import DatePicker from 'ant-design-vue/es/date-picker'
import Descriptions from 'ant-design-vue/es/descriptions'
import Divider from 'ant-design-vue/es/divider'
import Dropdown from 'ant-design-vue/es/dropdown'
import Empty from 'ant-design-vue/es/empty'
import Form from 'ant-design-vue/es/form'
import Input from 'ant-design-vue/es/input'
import InputNumber from 'ant-design-vue/es/input-number'
import Layout from 'ant-design-vue/es/layout'
import List from 'ant-design-vue/es/list'
import Menu from 'ant-design-vue/es/menu'
import Modal from 'ant-design-vue/es/modal'
import Progress from 'ant-design-vue/es/progress'
import Row from 'ant-design-vue/es/row'
import Segmented from 'ant-design-vue/es/segmented'
import Select from 'ant-design-vue/es/select'
import Skeleton from 'ant-design-vue/es/skeleton'
import Space from 'ant-design-vue/es/space'
import Spin from 'ant-design-vue/es/spin'
import Switch from 'ant-design-vue/es/switch'
import Table from 'ant-design-vue/es/table'
import Tag from 'ant-design-vue/es/tag'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import Home from './views/Home.vue'
import { isPushSupported, registerPushServiceWorker } from './services/pushNotifications'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Home',
      component: Home
    },
    {
      path: '/result',
      name: 'Result',
      component: () => import('./views/Result.vue')
    }
  ]
})

const app = createApp(App)

app.use(router)
const uiComponents = [
  Affix,
  Alert,
  BackTop,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  DatePicker,
  Descriptions,
  Divider,
  Dropdown,
  Empty,
  Form,
  Input,
  InputNumber,
  Layout,
  List,
  Menu,
  Modal,
  Progress,
  Row,
  Segmented,
  Select,
  Skeleton,
  Space,
  Spin,
  Switch,
  Table,
  Tag
]
uiComponents.forEach(component => app.use(component as any))

app.mount('#app')

if (isPushSupported()) {
  void registerPushServiceWorker().catch(error => {
    console.warn('[push] Service Worker registration failed:', error)
  })
}

