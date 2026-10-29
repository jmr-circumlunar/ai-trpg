<!--
  发牌动效（**公共组件**）：统一牌背 → 依次翻开露出身份牌面。

  为什么是组件而不是每页自己画：上一轮房间页与对局页各画了一份，逻辑必然漂移。
  现在只有一个实现：牌背地址与身份图都由**世界上下文**给（`backUrl` / `cards[].image`），
  组件不认识任何世界语义。

  用法：`<DealCards :cards="dealCards" :back-url="backUrl" @done="dealing = false" />`
-->
<template>
  <div v-if="cards.length" class="deal-overlay" @click="skip">
    <div class="deal-cards">
      <div
        v-for="(card, index) in cards"
        :key="card.key"
        class="deal-card"
        :class="{ flipped: index < flipped }"
      >
        <div class="deal-inner">
          <div class="deal-back card-back" :style="backStyle"></div>
          <div class="deal-front" :style="frontStyle(card)">
            <span class="deal-role">{{ card.role }}</span>
            <span class="deal-who">{{ card.name }}</span>
          </div>
        </div>
      </div>
    </div>
    <div class="deal-hint">{{ hint }}</div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";

const props = defineProps({
  // [{ key, name, role, image }] —— image 是身份牌面 URL（来自世界上下文）
  cards: { type: Array, default: () => [] },
  backUrl: { type: String, default: "" },
  hint: { type: String, default: "发牌中… 点击跳过" },
});

const emit = defineEmits(["done"]);

const flipped = ref(0);

onMounted(async () => {
  for (let index = 0; index <= props.cards.length; index += 1) {
    flipped.value = index;
    await new Promise((resolve) => setTimeout(resolve, 140));
  }
  emit("done");
});

function skip() {
  flipped.value = props.cards.length;
  emit("done");
}

function backStyle() {
  return props.backUrl ? { backgroundImage: `url(${props.backUrl})` } : {};
}

function frontStyle(card) {
  return card.image ? { backgroundImage: `url(${card.image})` } : {};
}
</script>
