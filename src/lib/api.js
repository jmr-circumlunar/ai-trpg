/**
 * 前端 API 层。
 *
 * 两件事：
 * 1. 统一错误处理（老页面有 `friendlyError`，这里保留同样的语义：给玩家看中文）
 * 2. SSE 解析：`advance/stream` 是 GET，用 fetch + ReadableStream 手写分帧
 *    （与老页面一致；`EventSource` 不能读 HTTP 错误体，这里要能给玩家报错）
 */

const JSON_HEADERS = { "Content-Type": "application/json" };

/** 把 HTTP 错误翻译成玩家看得懂的话。 */
export function friendlyError(err) {
  if (!err) return "出错了";
  const raw = String(err.message || err);
  if (raw.includes("Failed to fetch") || raw.includes("NetworkError")) {
    return "连不上后端：确认服务还在跑（默认 http://127.0.0.1:8000）";
  }
  const m = raw.match(/HTTP (\d{3})/);
  if (m) {
    const code = m[1];
    if (code === "404") return "没找到这个会话：它可能已经结束或被回收了";
    if (code === "409") return "上一步还没处理完，稍等一下再试";
    if (code === "422") return "提交的内容不合要求";
    if (code.startsWith("5")) return "后端出错了，看服务端日志";
    return `请求失败（${code}）`;
  }
  return raw.slice(0, 200);
}

export async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function postJson(url, body) {
  return sendJson(url, body, "POST");
}

/** 任意方法的 JSON 请求（PUT / DELETE 等；`postJson` 保留给只有 POST 的老调用）。 */
export async function sendJson(url, body, method = "POST") {
  const res = await fetch(url, {
    method,
    headers: JSON_HEADERS,
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json().catch(() => ({}));
}

function parseFrame(frame) {
  let event = "message";
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return { event, data: { raw: dataLines.join("\n") } };
  }
}

/**
 * 消费一次 SSE 回合流。
 * @param {string} url
 * @param {(event: string, data: object) => void} onEvent
 * @returns {Promise<object|null>} `done` 事件的 payload
 */
export async function streamTurn(url, onEvent) {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }
  if (!res.body) throw new Error("浏览器不支持流式响应");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const parsed = parseFrame(frame);
      if (!parsed) continue;
      if (parsed.event === "done") {
        result = parsed.data;
      } else if (parsed.event === "error") {
        throw new Error(parsed.data?.message || "回合出错");
      } else {
        onEvent(parsed.event, parsed.data || {});
      }
    }
  }
  return result;
}

/** 判定结果的徽标：后端给的是 `"success (18 vs 10)"` 这种串。 */
export function outcomeTier(outcome) {
  const raw = String(outcome || "");
  if (raw.startsWith("crit_success")) return "crit_success";
  if (raw.startsWith("success")) return "success";
  if (raw.startsWith("fail_forward")) return "fail_forward";
  if (raw.startsWith("failure")) return "failure";
  return "";
}
