/* Office.js Task Pane Controller for AI Paralegal Word Add-in */

function setStatusParagraph(container, text, color) {
  container.textContent = "";
  const p = document.createElement("p");
  p.style.fontSize = "12px";
  p.style.color = color;
  p.textContent = text;
  container.appendChild(p);
}

async function fetchRedlinePayload() {
  const apiBase = document.getElementById("apiBase").value.replace(/\/$/, "");
  const matterId = document.getElementById("matterId").value.trim();
  const jwt = document.getElementById("jwt").value.trim();
  const container = document.getElementById("list");
  setStatusParagraph(container, "Loading redlines...", "#475569");

  const resp = await fetch(`${apiBase}/matters/${encodeURIComponent(matterId)}/redlines/word-payload`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!resp.ok) {
    setStatusParagraph(container, `Error HTTP ${resp.status}`, "#b91c1c");
    return;
  }
  const data = await resp.json();
  container.textContent = "";

  for (const item of data.items || []) {
    const card = document.createElement("div");
    card.className = "card";

    const titleEl = document.createElement("div");
    titleEl.style.fontSize = "12px";
    titleEl.style.fontWeight = "700";
    titleEl.textContent = `[${String(item.severity || "info").toUpperCase()}] ${String(item.title || "")}`;

    const quoteEl = document.createElement("div");
    quoteEl.className = "quote";
    quoteEl.textContent = String(item.original_quote || "");

    const redlineEl = document.createElement("div");
    redlineEl.className = "redline";
    redlineEl.textContent = String(item.proposed_redline || "");

    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "Insert Fallback Clause + Comment";
    btn.addEventListener("click", () => void insertIntoWord(item));

    card.appendChild(titleEl);
    card.appendChild(quoteEl);
    card.appendChild(redlineEl);
    card.appendChild(btn);
    container.appendChild(card);
  }
}

async function insertIntoWord(item) {
  if (typeof Word === "undefined") {
    alert(`Preview fallback clause:\n\n${String(item.proposed_redline || "")}`);
    return;
  }
  await Word.run(async (context) => {
    const searchText = String(item.original_quote || "").slice(0, 120);
    const results = context.document.body.search(searchText, { matchCase: false });
    results.load("items");
    await context.sync();

    if (results.items.length > 0) {
      const target = results.items[0];
      target.insertText(String(item.proposed_redline || ""), Word.InsertLocation.replace);
      target.insertComment(
        `AI Paralegal [${String(item.category || "clause")}]: ${String(item.rationale || item.title || "")}`
      );
    } else {
      const p = context.document.body.insertParagraph(
        `[PROPOSED ${String(item.category || "CLAUSE").toUpperCase()}]: ${String(item.proposed_redline || "")}`,
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
