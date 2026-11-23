// Store-page chat: POSTs the message, reads the Server-Sent Events stream, renders tokens, status and product cards.
(() => {
  const root = document.querySelector(".store");
  const slug = root.dataset.slug;
  const $msgs = document.getElementById("messages");
  const $form = document.getElementById("composer");
  const $input = document.getElementById("input");
  const $send = document.getElementById("send");
  const $chat = document.getElementById("chat");
  let busy = false;

  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Tiny, safe markdown: escape first, then **bold**, *italic*, bullet lists and paragraphs.
  function md(text) {
    const lines = esc(text).split("\n");
    let html = "", list = false;
    for (const raw of lines) {
      const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|\s)\*(\S.+?)\*/g, "$1<i>$2</i>");
      const item = line.match(/^\s*(?:[-•*]|\d+\.)\s+(.*)/);
      if (item) { if (!list) { html += "<ul>"; list = true; } html += `<li>${item[1]}</li>`; continue; }
      if (list) { html += "</ul>"; list = false; }
      if (line.trim()) html += `<p>${line}</p>`;
    }
    return html + (list ? "</ul>" : "");
  }

  function bubble(role, text = "") {
    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.innerHTML = role === "user" ? esc(text) : md(text);
    $msgs.appendChild(el);
    scroll();
    return el;
  }
  const scroll = () => { $msgs.scrollTop = $msgs.scrollHeight; };

  function cards(items) {
    const wrap = document.createElement("div");
    wrap.className = "cards";
    for (const p of items) {
      const b = document.createElement("button");
      b.className = "mini" + (p.in_stock ? "" : " out");
      b.innerHTML = `<b>${esc(p.name)}</b><span>Rs ${p.price.toLocaleString()}</span>`;
      b.onclick = () => highlight(p.id);
      wrap.appendChild(b);
    }
    return wrap;
  }

  function highlight(id) {
    const el = document.querySelector(`.product[data-id="${id}"]`);
    if (!el) return;
    $chat.classList.remove("open");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("highlight");
    setTimeout(() => el.classList.remove("highlight"), 1800);
  }

  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    busy = true; $send.disabled = true; $input.value = "";
    document.getElementById("chips")?.remove();
    bubble("user", text);

    let status = document.createElement("div");
    status.className = "status"; status.textContent = "Thinking…";
    $msgs.appendChild(status); scroll();

    let bot = null, acc = "", seen = new Set();
    const ensureBot = () => { if (!bot) { bot = bubble("bot"); $msgs.appendChild(status); } return bot; };

    try {
      const res = await fetch(`/api/s/${slug}/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!res.ok) throw new Error(res.status === 422 ? "That message is too long." : `HTTP ${res.status}`);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          const ev = (block.match(/^event: (.*)$/m) || [])[1];
          const data = JSON.parse((block.match(/^data: (.*)$/m) || [, "{}"])[1]);
          if (ev === "token") { acc += data.text; ensureBot().innerHTML = md(acc); scroll(); }
          else if (ev === "status") {
            status.textContent = data.text;
            if (acc && !acc.endsWith("\n")) acc += "\n\n";   // keep "let me check" and the answer apart
          }
          else if (ev === "products") {
            const fresh = data.items.filter((p) => !seen.has(p.id));
            fresh.forEach((p) => seen.add(p.id));
            if (fresh.length) { $msgs.insertBefore(cards(fresh), status); scroll(); }
          }
          else if (ev === "blocked") { ensureBot().classList.add("blocked"); }
          else if (ev === "error") { bubble("error", data.text); }
        }
      }
    } catch (e) {
      bubble("error", e.message || "Connection lost. Please try again.");
    } finally {
      status.remove();
      busy = false; $send.disabled = false; $input.focus();
    }
  }

  async function loadHistory() {
    try {
      const res = await fetch(`/api/s/${slug}/history`);
      const { messages } = await res.json();
      if (messages.length) document.getElementById("chips")?.remove();
      for (const m of messages) bubble(m.role === "user" ? "user" : "bot", m.text);
    } catch { /* first visit or offline: keep the greeting */ }
  }

  $form.addEventListener("submit", (e) => { e.preventDefault(); send($input.value); });
  document.getElementById("chips")?.addEventListener("click", (e) => { if (e.target.tagName === "BUTTON") send(e.target.textContent); });
  document.querySelectorAll(".ask").forEach((b) =>
    b.addEventListener("click", () => { $chat.classList.add("open"); send(`Tell me about the ${b.dataset.name}`); }));
  document.getElementById("new-chat").addEventListener("click", async () => {
    await fetch(`/api/s/${slug}/new`, { method: "POST" });
    [...$msgs.querySelectorAll(".msg:not(.intro), .cards")].forEach((el) => el.remove());
  });
  document.getElementById("chat-fab").addEventListener("click", () => { $chat.classList.add("open"); $input.focus(); });
  loadHistory();
})();
