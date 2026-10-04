/* avernet.cc landing page behaviour: language, hero network, lifecycle tabs,
   copy buttons, demo dialog and the GitHub star count. No dependencies. */
(function () {
  "use strict";

  var root = document.documentElement;

  /* ---------- Language ----------
     English is in the markup. Chinese lives next to it in data-zh* attributes:
     data-zh (text), data-zh-html (trusted inline markup) and data-zh-<attr>. */
  var META = {
    en: {
      title: "Avernet · Organization-Scale Multi-Agent Coordination Platform",
      description: "Avernet is an open-source, organization-scale multi-agent coordination platform. Agents from any engine, and the people who own them, find each other, split the work and improve on real tasks."
    },
    zh: {
      title: "Avernet · 组织级多智能体协作平台",
      description: "Avernet 是开源的组织级多智能体协作平台。不同引擎、不同主人的 Agent 在这里互相发现、分工协作，在真实任务中持续进化。"
    }
  };
  var ATTRS = ["aria-label", "alt", "href", "src"];

  function camel(s) {
    return s.replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
  }
  function all(selector) {
    return Array.prototype.slice.call(document.querySelectorAll(selector));
  }

  // Remember the English originals once, before anything changes them.
  all("[data-zh]").forEach(function (el) { el.dataset.en = el.textContent; });
  all("[data-zh-html]").forEach(function (el) { el.dataset.enHtml = el.innerHTML; });
  ATTRS.forEach(function (attr) {
    all("[data-zh-" + attr + "]").forEach(function (el) {
      el.dataset[camel("en-" + attr)] = el.getAttribute(attr) || "";
    });
  });

  var lang = /^zh/i.test(root.lang) ? "zh" : "en";

  function applyLang(next) {
    var zh = next === "zh";
    lang = next;
    root.lang = zh ? "zh-CN" : "en";
    all("[data-zh]").forEach(function (el) { el.textContent = zh ? el.dataset.zh : el.dataset.en; });
    all("[data-zh-html]").forEach(function (el) { el.innerHTML = zh ? el.dataset.zhHtml : el.dataset.enHtml; });
    ATTRS.forEach(function (attr) {
      var zhKey = camel("zh-" + attr);
      var enKey = camel("en-" + attr);
      all("[data-zh-" + attr + "]").forEach(function (el) {
        el.setAttribute(attr, zh ? el.dataset[zhKey] : el.dataset[enKey]);
      });
    });
    document.title = META[next].title;
    var description = document.getElementById("meta-description");
    if (description) description.setAttribute("content", META[next].description);
    all("[data-lang-toggle]").forEach(function (button) {
      button.textContent = zh ? "EN" : "中文";
      button.setAttribute("aria-label", zh ? "Switch to English" : "切换到中文");
      button.setAttribute("lang", zh ? "en" : "zh-CN");
    });
  }

  if (lang === "zh") applyLang("zh");
  root.classList.remove("i18n-pending");

  all("[data-lang-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      applyLang(lang === "zh" ? "en" : "zh");
      try { localStorage.setItem("avernet-lang", lang); } catch (e) {}
      try {
        var url = new URL(location.href);
        url.searchParams.set("lang", lang);
        history.replaceState(null, "", url);
      } catch (e) {}
    });
  });

  /* ---------- Hero network ----------
     One orchestrated moment: scattered nodes glide into teams, the links draw,
     then a few messages start moving. Reduced motion shows the formed network. */
  var net = document.querySelector(".net");
  if (net) {
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var motions = Array.prototype.slice.call(net.querySelectorAll("animateMotion"));
    if (reduce) {
      net.classList.add("is-static", "is-assembled");
    } else {
      window.requestAnimationFrame(function () {
        setTimeout(function () { net.classList.add("is-assembled"); }, 250);
      });
      setTimeout(function () {
        net.classList.add("is-live");
        motions.forEach(function (motion, i) {
          setTimeout(function () {
            try {
              motion.beginElement();
              // Only show a message once it is on its path; before that it sits at the SVG origin.
              motion.parentNode.classList.add("is-moving");
            } catch (e) {}
          }, i * 900);
        });
      }, 2950);
      if ("IntersectionObserver" in window && typeof net.pauseAnimations === "function") {
        new IntersectionObserver(function (entries) {
          if (entries[0].isIntersecting) net.unpauseAnimations();
          else net.pauseAnimations();
        }).observe(net);
      }
    }
  }

  /* ---------- Lifecycle tabs ---------- */
  var tabs = all(".cycle-tab");
  function select(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
    });
    if (focus) tab.focus();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener("click", function () { select(tab, false); });
    tab.addEventListener("keydown", function (event) {
      var next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (i + 1) % tabs.length;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (i - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      if (next !== null) {
        event.preventDefault();
        select(tabs[next], true);
      }
    });
  });

  /* ---------- Copy buttons ---------- */
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) {}
      document.body.removeChild(area);
      if (ok) resolve(); else reject(new Error("copy failed"));
    });
  }
  all("[data-copy-target]").forEach(function (button) {
    button.addEventListener("click", function () {
      var source = document.getElementById(button.getAttribute("data-copy-target"));
      if (!source) return;
      var clone = source.cloneNode(true);
      Array.prototype.forEach.call(clone.querySelectorAll(".p"), function (prompt) { prompt.remove(); });
      copyText(clone.textContent.replace(/\s+$/, "")).then(function () {
        button.textContent = lang === "zh" ? "已复制" : "Copied";
      }, function () {
        button.textContent = lang === "zh" ? "请手动复制" : "Select to copy";
      }).then(function () {
        setTimeout(function () {
          button.textContent = lang === "zh" ? button.dataset.zh : button.dataset.en;
        }, 1600);
      });
    });
  });

  /* ---------- Demo dialog ---------- */
  var dialog = document.getElementById("demo-dialog");
  var video = dialog ? dialog.querySelector("video") : null;
  all("[data-demo-open]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (!dialog || typeof dialog.showModal !== "function") {
        window.location.href = "https://github.com/inclusionAI/Avernet#demo";
        return;
      }
      if (video && !video.getAttribute("src")) {
        video.preload = "metadata";
        video.src = video.getAttribute("data-src");
      }
      dialog.showModal();
    });
  });
  if (dialog) {
    var closeButton = dialog.querySelector("[data-demo-close]");
    if (closeButton) closeButton.addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("close", function () { if (video) video.pause(); });
    if (video) video.addEventListener("error", function () { dialog.classList.add("is-error"); });
  }

  /* ---------- GitHub stars ---------- */
  var starTargets = all("[data-stars]");
  function showStars(count) {
    var text = count >= 1000 ? (count / 1000).toFixed(1).replace(/\.0$/, "") + "k" : String(count);
    starTargets.forEach(function (el) {
      el.textContent = text;
      el.hidden = false;
    });
  }
  if (starTargets.length && window.fetch) {
    var cached = null;
    try { cached = JSON.parse(localStorage.getItem("avernet-stars") || "null"); } catch (e) {}
    if (cached && typeof cached.n === "number" && Date.now() - cached.t < 6 * 3600 * 1000) {
      showStars(cached.n);
    } else {
      fetch("https://api.github.com/repos/inclusionAI/Avernet", { headers: { Accept: "application/vnd.github+json" } })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) {
          if (!data || typeof data.stargazers_count !== "number") return;
          showStars(data.stargazers_count);
          try { localStorage.setItem("avernet-stars", JSON.stringify({ n: data.stargazers_count, t: Date.now() })); } catch (e) {}
        })
        .catch(function () {});
    }
  }
})();
