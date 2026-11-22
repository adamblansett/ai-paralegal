/* Office.js Task Pane Controller for AI Paralegal Word Add-in */

async function fetchRedlinePayload() {
  const apiBase = document.getElementById("apiBase").value.replace(/\/$/, "");
  const matterId = document.getElementById("matterId").value.trim();
  const jwt = document.getElementById("jwt").value.trim();
  const container = document.getElementById("list");
  container.innerHTML = "<p style='font-size:12px;color:#475569;'>Loading redlines...</p>";

  const resp = await fetch(`${apiBase}/matters/${matterId}/redlines/word-payload`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!resp.ok) {
    container.innerHTML = `<p style='font-size:12px;color:#b91c1c;'>Error HTTP ${resp.status}</p>`;
    return;
  }
  const data = await resp.json();
  container.innerHTML = "";
  for (const item of data.items) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div style="font-size:12px;font-weight:700;">[${item.severity.toUpperCase()}] ${item.title}</div>
      <div class="quote">${item.original_quote}</div>
      <div class="redline">${item.proposed_redline}</div>
    `;
    const btn = document.createElement("button");
    btn.textContent = "Insert Fallback Clause + Comment";
    btn.onclick = () => insertIntoWord(item);
    card.appendChild(btn);
    container.appendChild(card);
  }
}

async function insertIntoWord(item) {
  if (typeof Word === "undefined") {
    alert(`Preview fallback clause:\n\n${item.proposed_redline}`);
    return;
  }
  await Word.run(async (context) => {
    const searchText = item.original_quote.slice(0, 120);
    const results = context.document.body.search(searchText, { matchCase: false });
    results.load("items");
    await context.sync();

    if (results.items.length > 0) {
      const target = results.items[0];
      target.insertText(item.proposed_redline, Word.InsertLocation.replace);
      target.insertComment(`AI Paralegal [${item.category}]: ${item.rationale || item.title}`);
    } else {
      const p = context.document.body.insertParagraph(
        `[PROPOSED ${item.category.toUpperCase()}]: ${item.proposed_redline}`,
        Word.InsertLocation.end
      );
      p.font.color = "#15803d";
    }
    await context.sync();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("loadBtn");
  if (btn) btn.addEventListener("click", () => void fetchRedlinePayload());
  if (typeof Office !== "undefined" && typeof Office.onReady === "function") {
    Office.onReady(() => {});
  }
});
