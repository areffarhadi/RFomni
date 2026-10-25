(function () {
  var CHAT = "/";
  var STUDIO = "/studio";
  var HOME = "/hub";

  function token() {
    try {
      return localStorage.getItem("token") || "";
    } catch (e) {
      return "";
    }
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[ch];
    });
  }

  function session() {
    var t = token();
    if (!t) return Promise.resolve(null);
    return fetch("/api/v1/auths/", {
      headers: { Authorization: "Bearer " + t }
    })
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .catch(function () {
        return null;
      });
  }

  function current() {
    var path = location.pathname;
    if (path === "/studio" || path.indexOf("/studio/") === 0) return "studio";
    if (path === "/hub") return "home";
    if (path === "/auth") return "";
    return "chat";
  }

  function goProtected(path) {
    return function (event) {
      event.preventDefault();
      session().then(function (user) {
        if (!user) {
          location.href = "/auth?redirect=" + encodeURIComponent(path);
          return;
        }
        location.href = path;
      });
    };
  }

  function render(user) {
    var bar = document.getElementById("omni-site-nav");
    if (!bar) {
      bar = document.createElement("header");
      bar.id = "omni-site-nav";
      document.body.insertBefore(bar, document.body.firstChild);
    }
    var here = current();
    var name = user ? user.name || user.email || "" : "";
    var redirect = encodeURIComponent(location.pathname + location.search);
    bar.innerHTML =
      '<a class="brand" href="' + HOME + '">Omni</a>' +
      "<nav>" +
      '<a href="' + HOME + '" data-nav="home"' + (here === "home" ? ' class="active"' : "") + ">Home</a>" +
      '<a href="' + CHAT + '" data-nav="chat"' + (here === "chat" ? ' class="active"' : "") + ">Chat</a>" +
      '<a href="' + STUDIO + '" data-nav="studio"' + (here === "studio" ? ' class="active"' : "") + ">Omni Studio</a>" +
      "</nav>" +
      '<div class="account">' +
      (name
        ? '<span class="who">' + escapeHtml(name) + '</span><button type="button" id="omni-signout">Sign out</button>'
        : '<a class="signin" href="/auth?redirect=' + redirect + '">Sign in</a>') +
      "</div>";

    bar.querySelector('[data-nav="chat"]').addEventListener("click", goProtected(CHAT));
    bar.querySelector('[data-nav="studio"]').addEventListener("click", goProtected(STUDIO));
    var signOut = document.getElementById("omni-signout");
    if (signOut) {
      signOut.addEventListener("click", function () {
        try {
          localStorage.removeItem("token");
        } catch (e) {}
        document.cookie = "token=; Max-Age=0; path=/";
        location.href = "/auth";
      });
    }

    ["go-chat", "go-studio"].forEach(function (id) {
      var link = document.getElementById(id);
      if (!link) return;
      link.addEventListener("click", goProtected(link.getAttribute("href")));
    });

    var gate = document.getElementById("studio-gate");
    if (gate) {
      if (!user) {
        location.replace("/auth?redirect=" + encodeURIComponent(STUDIO));
        return;
      }
      gate.hidden = false;
      var who = document.getElementById("studio-user");
      if (who) who.textContent = name;
    }
  }

  function start() {
    session().then(render);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
