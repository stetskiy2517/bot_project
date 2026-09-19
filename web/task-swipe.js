(() => {
  "use strict";

  if (window.__plannerTaskSwipes) return;
  window.__plannerTaskSwipes = true;

  const HINT_KEY = "personal-secretary-task-swipe-hint-v1";
  const COMMIT_X = 72;
  const OPEN_X = 44;
  const WHEEL_X = 90;
  const DIRECTION_RATIO = 1.15;
  const app = document.getElementById("app");
  let touch = null;
  let suppressClickUntil = 0;
  let wheelRow = null;
  let wheelX = 0;
  let wheelTimer = null;
  let observer = null;

  function taskTabActive() {
    const tab = document.getElementById("libraryTasksTab");
    return Boolean(tab && (tab.classList.contains("active") || tab.getAttribute("aria-selected") === "true"));
  }

  function haptic() {
    try {
      if (typeof navigator.vibrate === "function") navigator.vibrate(8);
    } catch (_error) {}
  }

  function titleText(card) {
    return card.querySelector(".planner-task-title")?.textContent?.trim() || "задачу";
  }

  function primaryButton(card, actions) {
    const completed = card.classList.contains("completed");
    const reminderSelector = completed
      ? '[data-unified-reminder-action="reopen"]'
      : '[data-unified-reminder-action="complete"]';
    const reminder = actions.querySelector(reminderSelector);
    if (reminder) return reminder;
    const wanted = completed ? "Вернуть" : "Выполнено";
    return Array.from(actions.querySelectorAll("button")).find(button => button.textContent?.trim() === wanted) || null;
  }

  function addBell(card) {
    if (!card.classList.contains("planner-reminder-task")) return;
    const title = card.querySelector(".planner-task-title");
    if (!title || title.querySelector(".planner-task-bell")) return;
    const text = document.createElement("span");
    text.className = "planner-task-title-text";
    text.textContent = title.textContent || "Без названия";
    const bell = document.createElement("button");
    bell.type = "button";
    bell.className = "planner-task-bell";
    bell.dataset.reminderEdit = String(card.dataset.reminderId || "");
    bell.setAttribute("aria-label", "Настроить уведомление");
    bell.title = "Настроить уведомление";
    bell.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg>';
    title.replaceChildren(text, bell);
  }

  function actionWidth(row) {
    const rail = row.querySelector(".planner-task-swipe-actions");
    if (!rail || !rail.children.length) return 0;
    const measured = Math.ceil(rail.scrollWidth || rail.getBoundingClientRect().width || rail.children.length * 88);
    const max = Math.max(88, Math.floor((row.clientWidth || 360) * 0.82));
    return Math.min(measured, max);
  }

  function setOffset(row, offset, animate = true) {
    const card = row?.querySelector(":scope > .planner-task-card");
    if (!card) return;
    const width = actionWidth(row);
    const bounded = Math.max(-width, Math.min(104, Number(offset) || 0));
    card.style.transition = animate ? "transform .2s cubic-bezier(.22,.8,.24,1)" : "none";
    card.style.transform = `translateX(${bounded}px)`;
    row.dataset.offset = String(bounded);
    row.classList.toggle("show-primary", bounded > 8);
    row.classList.toggle("actions-open", bounded < -8);
  }

  function closeRows(except = null) {
    document.querySelectorAll("#libraryList .planner-task-swipe-row").forEach(row => {
      if (row !== except) setOffset(row, 0);
    });
  }

  function triggerPrimary(row) {
    if (!row || row.dataset.busy === "1") return;
    const button = row.querySelector(".planner-task-primary-proxy > button");
    if (!button || button.disabled) {
      setOffset(row, 0);
      return;
    }
    row.dataset.busy = "1";
    setOffset(row, 98);
    haptic();
    try {
      // Start the action immediately. Delaying the click can lose the command if
      // another async library refresh replaces the row during the animation.
      button.click();
    } finally {
      setTimeout(() => {
        if (row.isConnected) setOffset(row, 0);
        delete row.dataset.busy;
      }, 220);
    }
  }

  function revealActions(row) {
    const width = actionWidth(row);
    if (!width) {
      setOffset(row, 0);
      return;
    }
    closeRows(row);
    setOffset(row, -width);
    haptic();
  }

  function enhanceCard(card) {
    if (!card || card.dataset.taskSwipeReady === "1") return;
    const actions = card.querySelector(":scope > .planner-task-actions");
    if (!actions) return;
    const buttons = Array.from(actions.querySelectorAll(":scope > button"));
    if (!buttons.length) return;

    card.dataset.taskSwipeReady = "1";
    addBell(card);

    const row = document.createElement("div");
    row.className = "planner-task-swipe-row";
    if (card.classList.contains("subtask")) row.classList.add("subtask");
    const depth = card.style.getPropertyValue("--task-depth");
    if (depth) row.style.setProperty("--task-depth", depth);
    row.dataset.offset = "0";
    row.dataset.taskTitle = titleText(card);

    const primary = primaryButton(card, actions);
    const cue = document.createElement("div");
    cue.className = "planner-task-primary-cue";
    cue.innerHTML = card.classList.contains("completed")
      ? '<span aria-hidden="true">↩</span><b>Вернуть</b>'
      : '<span aria-hidden="true">✓</span><b>Готово</b>';

    const rail = document.createElement("div");
    rail.className = "planner-task-swipe-actions";
    const hidden = document.createElement("div");
    hidden.className = "planner-task-primary-proxy";
    hidden.setAttribute("aria-hidden", "true");

    for (const button of buttons) {
      if (button === primary) {
        hidden.appendChild(button);
      } else {
        button.classList.add("planner-task-swipe-action");
        const text = button.textContent?.trim() || "";
        if (text === "Удалить") button.classList.add("danger");
        rail.appendChild(button);
      }
    }
    actions.remove();

    card.parentNode?.insertBefore(row, card);
    row.append(cue, rail, hidden, card);
  }

  function enhanceAll() {
    if (!taskTabActive()) return;
    const list = document.getElementById("libraryList");
    if (!list) return;
    list.querySelectorAll(".planner-task-card").forEach(enhanceCard);
    syncTaskChrome();
    showHintOnce();
  }

  function syncTaskChrome() {
    const active = taskTabActive();
    const noteWrap = document.getElementById("notesProductToolbarWrap");
    if (noteWrap) noteWrap.hidden = active;
    const noteToolbar = document.getElementById("notesProductToolbar");
    if (active) noteToolbar?.classList.remove("visible");
    const noteStatus = document.getElementById("notesSearchStatus");
    if (active) noteStatus?.classList.remove("visible");
    const taskSearch = document.getElementById("plannerTaskSearchInput");
    if (active && taskSearch) taskSearch.placeholder = "Поиск по задачам";
  }

  function showHintOnce() {
    if (!taskTabActive() || document.getElementById("plannerTaskSwipeHint")) return;
    const list = document.getElementById("libraryList");
    if (!list?.querySelector(".planner-task-card")) return;
    try {
      if (localStorage.getItem(HINT_KEY) === "1") return;
    } catch (_error) {}
    const hint = document.createElement("div");
    hint.id = "plannerTaskSwipeHint";
    hint.className = "planner-task-swipe-hint";
    hint.textContent = "Смахните карточку вправо — выполнить. Влево — действия.";
    const filters = list.querySelector(".planner-task-filters");
    const toolbar = list.querySelector(".planner-task-toolbar");
    if (filters) filters.insertAdjacentElement("afterend", hint);
    else if (toolbar) toolbar.insertAdjacentElement("afterend", hint);
    else list.prepend(hint);
    try {
      localStorage.setItem(HINT_KEY, "1");
    } catch (_error) {}
    setTimeout(() => {
      hint.classList.add("hide");
      setTimeout(() => hint.remove(), 220);
    }, 7000);
  }

  function installStyles() {
    if (document.getElementById("taskSwipeStyles")) return;
    const style = document.createElement("style");
    style.id = "taskSwipeStyles";
    style.textContent = `
      .planner-task-swipe-row{position:relative;overflow:hidden;margin:0 0 10px;border-radius:18px;background:#e9e9e6;touch-action:pan-y}
      .planner-task-swipe-row.subtask{margin-left:calc(min(var(--task-depth),3) * 18px);border-radius:15px}
      .planner-task-swipe-row>.planner-task-card{position:relative;z-index:3;margin:0!important;background:#fff;will-change:transform;touch-action:pan-y}
      .planner-task-swipe-row.subtask>.planner-task-card{margin-left:0!important}
      .planner-task-primary-cue{position:absolute;z-index:1;inset:0 auto 0 0;width:104px;display:flex;align-items:center;justify-content:center;gap:6px;background:#e0ece3;color:#315c3b;font-size:12px;opacity:.72}
      .planner-task-primary-cue span{font-size:17px;line-height:1}.planner-task-primary-cue b{font-weight:700}
      .planner-task-swipe-actions{position:absolute;z-index:2;inset:0 0 0 auto;display:flex;justify-content:flex-end;align-items:stretch;max-width:82%;background:#e9e9e6}
      .planner-task-swipe-action{min-width:86px!important;height:100%;min-height:100%!important;padding:0 12px!important;border:0!important;border-radius:0!important;background:#e4e4e1!important;color:#383835!important;font-size:12px!important;font-weight:650!important;cursor:pointer;touch-action:manipulation}
      .planner-task-swipe-action.danger{background:#f2dddd!important;color:#8a2d2d!important}
      .planner-task-primary-proxy{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;pointer-events:none}
      .planner-task-title{display:flex;align-items:center;gap:7px}
      .planner-task-title-text{min-width:0;overflow-wrap:anywhere}
      .planner-task-bell{display:inline-flex;flex:0 0 auto;width:22px;height:22px;padding:2px;align-items:center;justify-content:center;border:0;border-radius:7px;background:transparent;color:#777772;cursor:pointer;touch-action:manipulation}
      .planner-task-bell:active{background:#ececea}
      .planner-task-bell svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      .planner-reminder-task.completed .planner-task-title{text-decoration:none!important;color:#777773}
      .planner-reminder-task.completed .planner-task-title-text{text-decoration:line-through;text-decoration-thickness:1.5px;text-decoration-color:#8c8c88}
      .planner-task-swipe-hint{margin:0 0 10px;padding:9px 11px;border-radius:12px;background:#efefec;color:#777772;font-size:11px;line-height:1.35;opacity:1;transition:opacity .2s ease}
      .planner-task-swipe-hint.hide{opacity:0}
      @media (prefers-reduced-motion:reduce){.planner-task-swipe-row>.planner-task-card,.planner-task-swipe-hint{transition:none!important}}
    `;
    document.head.appendChild(style);
  }

  function installGestures(list) {
    if (list.dataset.taskSwipeGestures === "1") return;
    list.dataset.taskSwipeGestures = "1";

    list.addEventListener("touchstart", event => {
      const row = event.target.closest?.(".planner-task-swipe-row");
      if (!row || event.touches.length !== 1 || event.target.closest("button, input, select, textarea")) {
        touch = null;
        return;
      }
      event.stopPropagation();
      closeRows(row);
      const point = event.touches[0];
      touch = {
        row,
        x: point.clientX,
        y: point.clientY,
        startOffset: Number(row.dataset.offset || 0),
        horizontal: false,
      };
    }, {passive:true});

    list.addEventListener("touchmove", event => {
      if (!touch?.row || event.touches.length !== 1) return;
      event.stopPropagation();
      const point = event.touches[0];
      const dx = point.clientX - touch.x;
      const dy = point.clientY - touch.y;
      if (!touch.horizontal) {
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
        if (Math.abs(dx) < Math.abs(dy) * DIRECTION_RATIO) return;
        touch.horizontal = true;
      }
      event.preventDefault();
      setOffset(touch.row, touch.startOffset + dx, false);
    }, {passive:false});

    list.addEventListener("touchend", event => {
      if (!touch || event.changedTouches.length !== 1) return;
      event.stopPropagation();
      const start = touch;
      touch = null;
      const point = event.changedTouches[0];
      const dx = point.clientX - start.x;
      const dy = point.clientY - start.y;
      const horizontal = start.horizontal || (Math.abs(dx) >= 38 && Math.abs(dx) >= Math.abs(dy) * DIRECTION_RATIO);
      if (!horizontal) {
        setOffset(start.row, start.startOffset);
        return;
      }
      event.preventDefault();
      const total = start.startOffset + dx;
      // Run the primary action before arming ghost-click suppression.
      // Otherwise the programmatic button.click() from triggerPrimary() is
      // intercepted by our own capture listener and a real touch swipe does
      // nothing even though wheel/trackpad tests pass.
      if (total >= COMMIT_X) triggerPrimary(start.row);
      else if (total <= -OPEN_X) revealActions(start.row);
      else setOffset(start.row, 0);
      suppressClickUntil = performance.now() + 400;
    }, {passive:false});

    list.addEventListener("touchcancel", () => {
      if (touch?.row) setOffset(touch.row, 0);
      touch = null;
    }, {passive:true});

    list.addEventListener("wheel", event => {
      const row = event.target.closest?.(".planner-task-swipe-row");
      if (!row || Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.05) return;
      event.preventDefault();
      event.stopPropagation();
      if (wheelRow && wheelRow !== row) {
        wheelX = 0;
        closeRows(row);
      }
      wheelRow = row;
      wheelX += event.deltaX;
      clearTimeout(wheelTimer);
      wheelTimer = setTimeout(() => { wheelX = 0; wheelRow = null; }, 180);
      if (Math.abs(wheelX) < WHEEL_X) return;
      if (wheelX > 0) revealActions(row);
      else triggerPrimary(row);
      wheelX = 0;
    }, {passive:false});

    list.addEventListener("click", event => {
      if (performance.now() < suppressClickUntil && event.target.closest(".planner-task-swipe-row")) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const action = event.target.closest(".planner-task-swipe-action");
      if (action) {
        setTimeout(() => closeRows(), 0);
        return;
      }
      const row = event.target.closest(".planner-task-swipe-row");
      if (row && Number(row.dataset.offset || 0) < -1) {
        event.preventDefault();
        event.stopPropagation();
        setOffset(row, 0);
      }
    }, true);
  }

  function install() {
    installStyles();
    const list = document.getElementById("libraryList");
    if (!list) {
      setTimeout(install, 80);
      return;
    }
    installGestures(list);
    if (!observer) {
      observer = new MutationObserver(() => setTimeout(enhanceAll, 0));
      observer.observe(list, {childList:true, subtree:true});
    }
    for (const id of ["libraryTasksTab", "libraryNotesTab", "libraryRemindersTab"]) {
      document.getElementById(id)?.addEventListener("click", () => setTimeout(() => {
        syncTaskChrome();
        enhanceAll();
      }, 0));
    }
    document.addEventListener("planner-library-changed", () => setTimeout(enhanceAll, 40));
    enhanceAll();
  }

  document.addEventListener("planner-ready", install);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install, {once:true});
  else setTimeout(install, 0);
})();