"use strict";

/* P8-0 shell — registers the service worker (real installability) and
 * keeps a minimal demo interaction so the base template is never empty.
 * The factory model customizes THIS file (and index.html/style.css);
 * manifest.json / sw.js / icons must stay intact. */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  });
}

const btn = document.getElementById("greetBtn");
const out = document.getElementById("output");
const net = document.getElementById("online");
if (btn && out) {
  btn.addEventListener("click", () => {
    out.textContent = "سلام از «" + document.title + "» — این پاسخ از خود اپ است.";
  });
}
function showNet() {
  if (!net) return;
  net.textContent = navigator.onLine
    ? "حالت: آنلاین"
    : "حالت: آفلاین — اجرا از کشِ Service Worker";
}
window.addEventListener("online", showNet);
window.addEventListener("offline", showNet);
showNet();
