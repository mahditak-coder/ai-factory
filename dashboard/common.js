/* AI Factory common — version shown from the single source /api/health. */
"use strict";
(async () => {
  try {
    const r = await fetch("/api/health");
    const h = await r.json();
    document.querySelectorAll(".af-version").forEach((e) => {
      e.textContent = "v" + (h.version || "?");
      e.title = "نسخه واقعی از /api/health";
    });
  } catch {
    document.querySelectorAll(".af-version").forEach((e) => e.textContent = "v ?" );
  }
})();
