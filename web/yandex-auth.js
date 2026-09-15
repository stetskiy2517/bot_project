(() => {
  "use strict";
  function install() {
    const google = document.getElementById("googleLogin");
    const card = google?.closest(".login-card");
    if (!google || !card || document.getElementById("yandexLogin")) return;
    const intro = card.querySelector(".muted");
    if (intro) intro.textContent = "Войди в приложение. Google Calendar можно подключить отдельно, когда он нужен для планирования.";
    const yandex = document.createElement("button");
    yandex.id = "yandexLogin";
    yandex.type = "button";
    yandex.className = "google-btn yandex-login-btn";
    yandex.textContent = "Войти через Яндекс";
    yandex.onclick = () => { location.href = "/auth/yandex/start"; };
    google.insertAdjacentElement("afterend", yandex);
    const note = document.createElement("p");
    note.className = "muted yandex-login-note";
    note.textContent = "Вход через Яндекс не даёт приложению доступ к календарю сам по себе.";
    yandex.insertAdjacentElement("afterend", note);
    const style = document.createElement("style");
    style.textContent = `
      .yandex-login-btn{margin-top:10px;background:#fff;color:#111;border:1px solid #ddd}
      .yandex-login-note{margin:10px 0 0;font-size:12px}
    `;
    document.head.append(style);
  }
  document.addEventListener("DOMContentLoaded", install);
  if (document.readyState !== "loading") install();
})();
