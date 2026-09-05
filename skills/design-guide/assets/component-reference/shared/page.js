(function (reference) {
  var spawnToast = reference.spawnToast;
  var pageOptions = reference.pageOptions || {};

  // Theme toggle
  var html = document.documentElement;
  var themeToggle = document.getElementById("themeToggleBtn");
  var themeIconUse = document.getElementById("themeIconUse");

  function updateTheme(theme) {
    html.dataset.theme = theme;
    if (theme === "light") {
      themeIconUse.setAttribute("href", "#lucide-moon");
      themeToggle.setAttribute("aria-label", "Switch to dark theme");
      themeToggle.setAttribute("title", "Switch to dark theme");
    } else {
      themeIconUse.setAttribute("href", "#lucide-sun");
      themeToggle.setAttribute("aria-label", "Switch to light theme");
      themeToggle.setAttribute("title", "Switch to light theme");
    }
  }

  function toggleTheme() {
    var next = html.dataset.theme === "dark" ? "light" : "dark";
    updateTheme(next);
  }

  themeToggle.addEventListener("click", toggleTheme);

  // Page export
  function buildMarkdownExport() {
    var lines = ["# " + (pageOptions.title || "Component Reference"), ""];
    document.querySelectorAll(".component-section").forEach(function (section) {
      var heading = section.querySelector("h1, h2");
      if (!heading) return;

      var title = heading.textContent.trim();
      var bodyLines = section.innerText.split("\n").map(function (line) {
        return line.trim();
      }).filter(Boolean);
      if (bodyLines[0] === title) bodyLines.shift();

      lines.push("## " + title, "");
      if (bodyLines.length) lines.push(bodyLines.join("\n\n"), "");
    });
    return lines.join("\n").trim() + "\n";
  }

  function downloadText(filename, contents, mimeType) {
    var url = URL.createObjectURL(new Blob([contents], { type: mimeType }));
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
  }

  function exportMarkdown() {
    downloadText(pageOptions.exportFilename || "component-reference.md", buildMarkdownExport(), "text/markdown;charset=utf-8");
    spawnToast("Markdown export downloaded", false);
  }

  // Share one section index between desktop navigation and the mobile drawer.
  var sections = Array.from(document.querySelectorAll(".component-section"));
  var toc = document.getElementById("componentIndex");
  var tocHome = toc.parentElement;
  var tocLinks = Array.from(toc.querySelectorAll(".toc-link"));
  var tocTrigger = document.getElementById("tocTrigger");
  var tocDrawer = document.getElementById("tocDrawer");
  var tocBody = document.getElementById("tocDrawerBody");
  var mobileNavigation = window.matchMedia("(max-width: 900px)");
  var tocDestination = null;

  function currentSection() {
    // A dense dashboard can show several destinations side by side without scrolling.
    var destination = sections.find(function (section) { return "#" + section.id === window.location.hash; });
    if (destination) {
      var top = destination.getBoundingClientRect().top;
      if (top >= 0 && top < window.innerHeight) return destination;
    }
    var current = sections[0];
    var nearestTop = -Infinity;
    sections.forEach(function (section) {
      var top = section.getBoundingClientRect().top;
      if (top <= 120 && top > nearestTop) { current = section; nearestTop = top; }
    });
    return current;
  }

  function updateContents() {
    var current = currentSection();
    tocLinks.forEach(function (link) {
      var selected = link.hash === "#" + current.id;
      link.classList.toggle("active", selected);
      if (selected) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
  }

  function openContents() {
    if (!mobileNavigation.matches || tocDrawer.open) return;
    tocDestination = null;
    updateContents();
    tocBody.appendChild(toc);
    html.classList.add("toc-open");
    tocDrawer.showModal();
    tocTrigger.setAttribute("aria-expanded", "true");
    var current = toc.querySelector(".active") || tocLinks[0];
    current.focus({ preventScroll: true });
    current.scrollIntoView({ block: "nearest", behavior: "instant" });
  }

  function closeContents(destination) {
    tocDestination = destination || null;
    tocDrawer.close();
  }

  function outsideContents(event) {
    var rect = tocDrawer.getBoundingClientRect();
    return event.target === tocDrawer && (event.clientX < rect.left || event.clientX > rect.right
      || event.clientY < rect.top || event.clientY > rect.bottom);
  }

  tocTrigger.addEventListener("click", openContents);
  document.getElementById("tocClose").addEventListener("click", function () { closeContents(); });
  tocDrawer.addEventListener("click", function (event) {
    if (outsideContents(event)) closeContents();
  });
  toc.addEventListener("click", function (event) {
    var link = event.target.closest(".toc-link");
    if (!tocDrawer.open || !link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button) return;
    var destination = document.getElementById(link.hash.slice(1));
    if (!destination) return;
    event.preventDefault();
    closeContents(destination);
  });
  tocDrawer.addEventListener("keydown", function (event) {
    if (event.key !== "Tab") return;
    var controls = [document.getElementById("tocClose")].concat(tocLinks);
    if (event.shiftKey && document.activeElement === controls[0]) {
      event.preventDefault(); controls[controls.length - 1].focus();
    } else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) {
      event.preventDefault(); controls[0].focus();
    }
  });
  tocDrawer.addEventListener("close", function () {
    if (tocDrawer.open) return;
    tocHome.appendChild(toc);
    html.classList.remove("toc-open");
    tocTrigger.setAttribute("aria-expanded", "false");
    var destination = tocDestination;
    tocDestination = null;
    if (destination) {
      window.history.pushState(null, "", "#" + destination.id);
      destination.scrollIntoView({ block: "start", behavior: "instant" });
      var heading = destination.querySelector("h1, h2");
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
      heading.addEventListener("blur", function () { heading.removeAttribute("tabindex"); }, { once: true });
    } else if (mobileNavigation.matches) {
      tocTrigger.focus({ preventScroll: true });
    } else {
      (toc.querySelector(".active") || tocLinks[0]).focus({ preventScroll: true });
    }
    updateContents();
  });
  mobileNavigation.addEventListener("change", function () {
    if (!mobileNavigation.matches && tocDrawer.open) closeContents();
  });
  window.addEventListener("scroll", updateContents, { passive: true });
  window.addEventListener("resize", updateContents);
  window.addEventListener("hashchange", updateContents);
  updateContents();

  reference.currentSection = currentSection;
  reference.updateContents = updateContents;
  reference.toggleTheme = toggleTheme;
  reference.exportMarkdown = exportMarkdown;
  reference.downloadText = downloadText;
}(window.ComponentReference));
