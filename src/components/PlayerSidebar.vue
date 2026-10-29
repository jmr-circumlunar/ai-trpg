<!--
  左侧角色栏 + 右侧世界信息。
  角色卡可点开档案抽屉（`#chars` 里的 `.profile-link` 语义）。
-->
<template>
  <aside class="panel">
    <h2>角色</h2>
    <div id="chars">
      <div v-if="!characters.length" class="empty">尚未开局</div>
      <div
        v-for="char in characters"
        :key="char.id"
        class="card profile-link"
        :class="{ 'is-dead': char.position === 'dead' }"
        :data-cid="char.id"
        @click="$emit('open-profile', char.id)"
      >
        <div class="name">{{ char.name }}</div>
        <div class="meta">{{ char.profession || "—" }}</div>
        <div v-if="char.hp !== undefined" class="bar">
          <i :style="{ width: hpPercent(char) + '%' }"></i>
        </div>
      </div>
    </div>
  </aside>

  <aside class="panel">
    <h2>世界</h2>
    <div id="world-info">
      <div v-if="!worldInfo || !worldInfo.name" class="empty">—</div>
      <template v-else>
        <div class="kv"><span>世界</span><span>{{ worldInfo.name }}</span></div>
        <div v-if="worldInfo.region" class="kv"><span>地点</span><span>{{ worldInfo.region }}</span></div>
        <div v-if="worldInfo.clock" class="kv"><span>时间</span><span>{{ worldInfo.clock }}</span></div>
        <div v-if="worldInfo.stage !== undefined" class="kv">
          <span>幕次</span><span>{{ worldInfo.stage }}</span>
        </div>
        <div v-if="worldInfo.tension !== undefined" class="kv">
          <span>张力</span><span>{{ worldInfo.tension }}</span>
        </div>
      </template>
    </div>
  </aside>
</template>

<script setup>
defineProps({
  characters: { type: Array, default: () => [] },
  worldInfo: { type: Object, default: null },
});

defineEmits(["open-profile"]);

function hpPercent(char) {
  const max = Number(char.hp_max || 0);
  const cur = Number(char.hp || 0);
  if (!max) return 0;
  return Math.max(0, Math.min(100, Math.round((cur / max) * 100)));
}
</script>
