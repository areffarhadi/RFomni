(function () {
  var CHAT = "/";
  // the studio: port 8710 on the LAN address; behind the public domain the chat is chat.<domain>, the studio <domain>
  var STUDIO = location.port
    ? location.protocol + "//" + location.hostname + ":8710/"
    : location.protocol + "//" + location.hostname.replace(/^chat\./, "") + "/";
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
    var signOut = document.getElementById("omni-signout");
    if (signOut) {
      signOut.addEventListener("click", function () {
        // the chat's own sign-out clears its session cookie, which scripts cannot reach; that cookie is also the
        // studio's session, so this signs you out of both
        var t = token();
        fetch("/api/v1/auths/signout", { method: "POST", headers: t ? { Authorization: "Bearer " + t } : {} })
          .catch(function () {})
          .then(function () {
            try {
              localStorage.removeItem("token");
            } catch (e) {}
            document.cookie = "token=; Max-Age=0; path=/";
            location.href = "/auth";
          });
      });
    }

    ["go-chat"].forEach(function (id) {
      var link = document.getElementById(id);
      if (!link) return;
      link.addEventListener("click", goProtected(link.getAttribute("href")));
    });

    var gate = document.getElementById("studio-gate");
    if (gate) {
      location.replace(STUDIO);  // the old /studio page: go to the real studio
      return;
    }
  }

  // The chat signs in without reloading the page (its sign-in page, or the session the studio's sign-in left), so
  // the bar is drawn again whenever the session changes, not only on load
  var shown = null;
  function refresh() {
    var t = token();
    if (t === shown) return;
    shown = t;
    session().then(render);
  }

  function start() {
    refresh();
    setInterval(refresh, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
