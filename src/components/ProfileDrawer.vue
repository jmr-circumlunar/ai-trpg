<!--
  角色档案抽屉（右侧滑出）。
  数据全部来自后端的 profile 接口，**前端不做任何推断**。
-->
<template>
  <div
    id="profile-drawer"
    class="drawer"
    :class="{ open }"
    :aria-hidden="!open"
    @click.self="$emit('close')"
  >
    <button id="pd-close" class="drawer-close" type="button" @click="$emit('close')">关闭</button>
    <div id="pd-body">
      <div v-if="!profile" class="empty">读取档案…</div>
      <template v-else>
        <h3>{{ profile.basic?.name || "—" }}</h3>
        <div class="muted">
          {{ profile.basic?.profession || "" }}
          <template v-if="profile.basic?.path_label">· {{ profile.basic.path_label }}</template>
        </div>

        <h4>当前</h4>
        <div class="row">
          <span>HP</span><span>{{ profile.now?.hp }} / {{ profile.now?.hp_max }}</span>
        </div>
        <div class="row">
          <span>理智</span><span>{{ profile.now?.sanity }} / {{ profile.now?.sanity_max }}</span>
        </div>
        <div class="row">
          <span>钱 / 补给</span><span>{{ profile.now?.gold }} / {{ profile.now?.supplies }}</span>
        </div>
        <div v-if="profile.now?.conditions?.length" class="row">
          <span>状态</span><span>{{ profile.now.conditions.join("、") }}</span>
        </div>

        <template v-if="profile.gear && profile.gear.length">
          <h4>装备</h4>
          <div v-for="(item, index) in profile.gear" :key="index" class="ev">
            <b>{{ item.name }}</b>
            <span class="muted"> · {{ item.quality }} · {{ (item.tags || []).join("、") }}</span>
          </div>
        </template>

        <template v-if="profile.timeline && profile.timeline.length">
          <h4>经历</h4>
          <div v-for="(ev, index) in profile.timeline" :key="index" class="ev">
            <b>第 {{ ev.turn }} 回合</b> — {{ ev.summary }}
          </div>
        </template>
      </template>
    </div>
  </div>
</template>

<script setup>
defineProps({
  open: { type: Boolean, default: false },
  profile: { type: Object, default: null },
});

defineEmits(["close"]);
</script>
