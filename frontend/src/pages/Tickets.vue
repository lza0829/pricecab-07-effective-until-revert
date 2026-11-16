<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const skus = ref([])
const tickets = ref([])
const msg = ref('')
const err = ref('')
const form = reactive({
  title: '',
  effective_at: '2026-11-16T10:00:00Z',
  effective_until: '',
  note: '',
  sku_code: '',
  new_price: '',
})
const lines = ref([])

const STATUS_LABEL = {
  scheduled: '待生效',
  applied: '已应用',
  reverted: '已回落',
  expired: '已过期',
  void: '已作废',
}

function statusClass(s) {
  if (s === 'applied') return 'ok'
  if (s === 'scheduled') return 'warn'
  if (s === 'expired') return 'bad'
  return 'muted'
}

async function refresh() {
  skus.value = await api.skus()
  tickets.value = await api.tickets()
  if (!form.sku_code && skus.value.length) form.sku_code = skus.value[0].sku_code
}

function addLine() {
  if (!form.sku_code || form.new_price === '') {
    err.value = '请选择商品并填写新价'
    return
  }
  lines.value.push({ sku_code: form.sku_code, new_price: Number(form.new_price) })
  form.new_price = ''
  err.value = ''
}

function removeLine(i) {
  lines.value.splice(i, 1)
}

async function submit() {
  msg.value = ''
  err.value = ''
  try {
    const t = await api.createTicket({
      title: form.title,
      effective_at: form.effective_at,
      effective_until: form.effective_until || null,
      note: form.note || null,
      lines: lines.value,
    })
    msg.value = `已创建 ${t.ticket_no}`
    form.title = ''
    form.note = ''
    form.effective_until = ''
    lines.value = []
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function voidTicket(t) {
  msg.value = ''
  err.value = ''
  try {
    await api.voidTicket(t.id)
    msg.value = `已作废 ${t.ticket_no}（截止前作废将取消未来回落）`
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

onMounted(() => refresh().catch((e) => (err.value = e.message)))
</script>

<template>
  <section class="hero">
    <h1>改价单</h1>
    <p>为商品预约新价与生效时刻，可选生效截止。未到业务钟前不会写入现行价签；过了截止的已应用单须走回落批次。</p>
  </section>

  <div class="panel">
    <h2>新建改价单</h2>
    <div class="row">
      <div class="field"><label>标题</label><input v-model="form.title" placeholder="例如：午间茶饮促销售价" /></div>
      <div class="field"><label>生效时刻 (UTC)</label><input v-model="form.effective_at" /></div>
      <div class="field"><label>生效截止 (UTC，可空)</label><input v-model="form.effective_until" placeholder="留空 = 不回落" /></div>
      <div class="field" style="flex:1"><label>备注</label><input v-model="form.note" /></div>
    </div>
    <div class="row">
      <div class="field">
        <label>商品</label>
        <select v-model="form.sku_code">
          <option v-for="s in skus" :key="s.sku_code" :value="s.sku_code">
            {{ s.sku_code }} · {{ s.title }}（现价 {{ s.current_price }}）
          </option>
        </select>
      </div>
      <div class="field"><label>新价</label><input v-model="form.new_price" type="number" step="0.01" /></div>
      <button class="btn ghost" type="button" @click="addLine">加入明细</button>
    </div>
    <table v-if="lines.length">
      <thead><tr><th>SKU</th><th>新价</th><th></th></tr></thead>
      <tbody>
        <tr v-for="(l, i) in lines" :key="i">
          <td>{{ l.sku_code }}</td>
          <td>{{ l.new_price }}</td>
          <td><button class="btn ghost" type="button" @click="removeLine(i)">移除</button></td>
        </tr>
      </tbody>
    </table>
    <div class="row" style="margin-top:12px">
      <button class="btn" type="button" @click="submit">提交改价单</button>
    </div>
    <p v-if="msg" class="msg ok">{{ msg }}</p>
    <p v-if="err" class="msg err">{{ err }}</p>
  </div>

  <div class="panel">
    <h2>改价单列表</h2>
    <table>
      <thead>
        <tr><th>单号</th><th>标题</th><th>生效时刻</th><th>生效截止</th><th>行数</th><th>状态</th><th>应用批次</th><th>回落批次</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="t in tickets" :key="t.id">
          <td>{{ t.ticket_no }}</td>
          <td>{{ t.title }}</td>
          <td>{{ t.effective_at }}</td>
          <td>{{ t.effective_until || '—' }}</td>
          <td>{{ t.line_count }}</td>
          <td>
            <span class="tag" :class="statusClass(t.status)">{{ STATUS_LABEL[t.status] || t.status }}</span>
          </td>
          <td>{{ t.apply_batch_id || '—' }}</td>
          <td>{{ t.revert_batch_id || '—' }}</td>
          <td>
            <button
              v-if="t.status === 'scheduled' || t.status === 'applied'"
              class="btn ghost"
              type="button"
              @click="voidTicket(t)"
            >作废</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
