<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const clock = ref('')
const batches = ref([])
const msg = ref('')
const err = ref('')
const result = ref(null)
const strategy = ref('list_price')

const STRATEGY_LABEL = {
  list_price: '回落到牌价 list_price',
  previous_price: '回落到截止前上一有效价',
}

async function refresh() {
  const c = await api.clock()
  clock.value = c.business_clock || ''
  strategy.value = c.revert_strategy || 'list_price'
  batches.value = await api.batches()
}

async function saveClock() {
  msg.value = ''
  err.value = ''
  try {
    await api.setClock(clock.value)
    msg.value = '业务钟已更新'
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function saveStrategy() {
  msg.value = ''
  err.value = ''
  try {
    await api.setRevertStrategy(strategy.value)
    msg.value = `回落目标策略已切换：${STRATEGY_LABEL[strategy.value]}`
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function applyDue() {
  msg.value = ''
  err.value = ''
  result.value = null
  try {
    result.value = await api.applyDue()
    msg.value = result.value.applied
      ? `已应用 ${result.value.ticket_count} 张改价单`
      : result.value.message
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function revertDue() {
  msg.value = ''
  err.value = ''
  result.value = null
  try {
    result.value = await api.revertDue()
    msg.value = result.value.reverted
      ? `已回落 ${result.value.ticket_count} 张改价单（批次 ${result.value.batch_no}）`
      : result.value.message
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

onMounted(() => refresh().catch((e) => (err.value = e.message)))
</script>

<template>
  <section class="hero">
    <h1>生效应用</h1>
    <p>拨动业务钟后，点「应用到期」将 effective_at ≤ 业务钟的改价单写入现行价签；业务钟越过生效截止的已应用单，须点「回落到期」走回落批次。两者互斥：有待回落单时应用被拒。</p>
  </section>

  <div class="panel">
    <h2>业务钟</h2>
    <div class="row">
      <div class="field" style="flex:1">
        <label>业务钟 (UTC)</label>
        <input v-model="clock" />
      </div>
      <button class="btn ghost" type="button" @click="saveClock">保存业务钟</button>
      <button class="btn warn" type="button" @click="applyDue">应用到期</button>
      <button class="btn" type="button" @click="revertDue">回落到期</button>
    </div>
    <div class="row">
      <div class="field" style="flex:1">
        <label>回落目标策略（全局一种，互斥）</label>
        <select v-model="strategy">
          <option value="list_price">{{ STRATEGY_LABEL.list_price }}</option>
          <option value="previous_price">{{ STRATEGY_LABEL.previous_price }}</option>
        </select>
      </div>
      <button class="btn ghost" type="button" @click="saveStrategy">保存策略</button>
    </div>
    <p v-if="msg" class="msg ok">{{ msg }}</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <pre v-if="result" style="margin-top:12px;font-size:0.82rem;color:var(--muted);white-space:pre-wrap">{{ JSON.stringify(result, null, 2) }}</pre>
  </div>

  <div class="panel">
    <h2>应用 / 回落批次</h2>
    <table>
      <thead>
        <tr><th>批次</th><th>类型</th><th>业务钟</th><th>改价单数</th><th>明细行</th><th>状态</th><th>时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id">
          <td>{{ b.batch_no }}</td>
          <td><span class="tag" :class="b.kind === 'revert' ? 'warn' : 'ok'">{{ b.kind === 'revert' ? '回落' : '应用' }}</span></td>
          <td>{{ b.clock_at }}</td>
          <td>{{ b.ticket_count }}</td>
          <td>{{ b.line_count }}</td>
          <td><span class="tag ok">{{ b.status }}</span></td>
          <td>{{ b.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
