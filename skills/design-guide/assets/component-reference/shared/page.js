(function (reference) {
  var spawnToast = reference.spawnToast;

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
    var lines = ["# Component Reference", ""];
    document.querySelectorAll(".component-section").forEach(function (section) {
      var heading = section.querySelector("h2");
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
    downloadText("component-reference.md", buildMarkdownExport(), "text/markdown;charset=utf-8");
    spawnToast("Markdown export downloaded", false);
  }

  // Sidebar scroll spy
  var sections = document.querySelectorAll(".component-section");
  var tocLinks = document.querySelectorAll(".toc-link");

  window.addEventListener("scroll", function () {
    var scrollPos = window.scrollY + 120;
    sections.forEach(function (sec) {
      var top = sec.offsetTop;
      var height = sec.offsetHeight;
      if (scrollPos >= top && scrollPos < top + height) {
        tocLinks.forEach(function (l) {
          l.classList.toggle("active", l.getAttribute("href") === "#" + sec.id);
        });
      }
    });
  });

  reference.toggleTheme = toggleTheme;
  reference.exportMarkdown = exportMarkdown;
  reference.downloadText = downloadText;
}(window.ComponentReference));
