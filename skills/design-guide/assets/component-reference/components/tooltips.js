(function () {
  document.querySelectorAll(".tooltip-wrapper, .meta-hash-wrapper").forEach(function (wrapper) {
    var trigger = wrapper.querySelector("button");
    var tooltip = wrapper.querySelector(".tooltip-bubble");
    var description = trigger.getAttribute("aria-describedby") || "";
    var timer = 0;
    var pointerInside = false;
    var focused = false;
    var suppressed = false;

    function position() {
      if (tooltip.hidden) return;
      var viewport = window.visualViewport;
      var left = viewport ? viewport.offsetLeft : 0;
      var top = viewport ? viewport.offsetTop : 0;
      var width = viewport ? viewport.width : document.documentElement.clientWidth;
      var height = viewport ? viewport.height : window.innerHeight;
      tooltip.style.maxWidth = Math.min(360, width - 24) + "px";
      var anchor = trigger.getBoundingClientRect();
      var bubble = tooltip.getBoundingClientRect();
      var x = anchor.left + (anchor.width - bubble.width) / 2;
      var y = anchor.top - bubble.height - 8;
      if (y < top + 12) y = anchor.bottom + 8;
      tooltip.style.left = Math.max(left + 12, Math.min(left + width - bubble.width - 12, x)) + "px";
      tooltip.style.top = Math.max(top + 12, Math.min(top + height - bubble.height - 12, y)) + "px";
    }

    function hide() {
      window.clearTimeout(timer);
      tooltip.hidden = true;
      wrapper.classList.remove("is-tooltip-visible");
      if (description) trigger.setAttribute("aria-describedby", description);
      else trigger.removeAttribute("aria-describedby");
    }

    function update() {
      hide();
      if (suppressed || (!pointerInside && !focused)) return;
      timer = window.setTimeout(function () {
        tooltip.hidden = false;
        position();
        wrapper.classList.add("is-tooltip-visible");
        trigger.setAttribute("aria-describedby", (description + " " + tooltip.id).trim());
      }, 150);
    }

    wrapper.addEventListener("pointerenter", function (event) {
      if (event.pointerType === "touch") return;
      pointerInside = true; suppressed = false; update();
    });
    wrapper.addEventListener("pointerleave", function () {
      pointerInside = false;
      if (!focused) suppressed = false;
      update();
    });
    trigger.addEventListener("focus", function () { focused = true; suppressed = false; update(); });
    trigger.addEventListener("blur", function () {
      focused = false;
      if (!pointerInside) suppressed = false;
      update();
    });
    window.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { suppressed = true; hide(); }
    });
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", position);
      window.visualViewport.addEventListener("scroll", position);
    }
  });
}());
