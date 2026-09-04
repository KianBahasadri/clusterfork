(function () {
  // Tabs
  var tabBtns = document.querySelectorAll(".tab-btn");
  var tabPanels = document.querySelectorAll(".tab-panel");
  tabBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      tabBtns.forEach(function (b) {
        b.setAttribute("aria-selected", "false");
        b.setAttribute("tabindex", "-1");
      });
      tabPanels.forEach(function (p) { p.classList.remove("active"); });

      btn.setAttribute("aria-selected", "true");
      btn.setAttribute("tabindex", "0");
      var targetId = btn.getAttribute("aria-controls");
      document.getElementById(targetId).classList.add("active");
    });
  });
}());
