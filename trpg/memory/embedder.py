"""Embedding 抽象。

- MiniMaxEmbedder：`POST /v1/embeddings {"model":"embo-01","texts":[...],"type":"db|query"}`
  注意 MiniMax 的参数名是 `texts`、且有 `type` 区分文档/查询，和 OpenAI 不同。
- HashEmbedder：离线降级实现（字符 n-gram 哈希），保证测试与断网时链路不断。

doc: 03-模块/M11-记忆/00-模块总述.md
module: M11
layer: L2
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

# 注意：`..llm` 在导入期会反向引用 `memory`（agent → recall → vector_store），
# 顶层 import 会形成循环。放在调用处延迟导入即可打破环。


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str], kind: str = "db") -> list[list[float]]: ...


class MiniMaxEmbedder:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.minimaxi.com/v1",
        model: str = "embo-01",
        timeout: float = 30.0,
        batch: int = 16,
    ) -> None:
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/embeddings"
        self.model = model
        self.timeout = timeout
        self.batch = batch
        self.dim = 1536
        self.calls = 0
        self.tokens = 0

    def embed(self, texts: list[str], kind: str = "db") -> list[list[float]]:
    
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch):
            chunk = texts[start : start + self.batch]
            data = post_json(
                self.url,
                {"model": self.model, "texts": chunk, "type": kind},
                {"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            base = data.get("base_resp") or {}
            if base.get("status_code", 0) not in (0, None):
                raise RuntimeError(f"embedding 失败 {base.get('status_code')}: {base.get('status_msg')}")
            vectors = data.get("vectors") or []
            if len(vectors) != len(chunk):
                raise RuntimeError(f"返回向量数不匹配：{len(vectors)} != {len(chunk)}")
            if vectors:
                self.dim = len(vectors[0])
            self.calls += 1
            self.tokens += int(data.get("total_tokens") or 0)
            out.extend(vectors)
        return out

    def probe(self) -> int:
        """探活 + 维度自检。返回实际维度；失败抛异常（调用方决定是否降级）。"""
        vecs = self.embed(["探活：雨夜的码头"], kind="query")
        if not vecs:
            raise RuntimeError("embedding 探活失败：返回空向量")
        dim = len(vecs[0])
        if self.dim and dim != self.dim:
            raise RuntimeError(f"embedding 维度漂移：{self.dim} → {dim}")
        self.dim = dim
        return dim


def probe_minimax_embedder(api_key: str, base_url: str, model: str = "embo-01") -> int:
    """探活：真调一次，返回维度。失败直接抛——静默降级是「记不住」的根源。"""
    emb = MiniMaxEmbedder(api_key, base_url=base_url, model=model)
    return emb.probe()


class HashEmbedder:
    """离线降级：字符 3-gram 哈希到固定维度。

    不是语义向量，但能把「字面相近」排到前面；用于测试与断网兜底。
    换成真 embedding 时只替换这个类，其他代码不动。
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        text = (text or "").strip()
        if not text:
            return vec
        # 单字 + 双字：单字保证有共享桶，双字保证局部顺序敏感
        grams = list(text) + [text[i : i + 2] for i in range(len(text) - 1)]
        for gram in grams:
            h = int.from_bytes(hashlib.md5(gram.encode("utf-8")).digest()[:4], "big")
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str], kind: str = "db") -> list[list[float]]:
        return [self._one(t) for t in texts]
