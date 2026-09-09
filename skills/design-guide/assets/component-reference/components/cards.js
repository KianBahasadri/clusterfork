(function (reference) {
  var instances = new WeakMap();
  var sequence = 0;
  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function adjustStreamlinedTitles(root) {
    var titles = root.querySelectorAll(".task-card .task-card-title, .task-card-title");
    titles.forEach(function (title) {
      title.style.fontSize = "";
      if (title.scrollWidth > title.clientWidth) {
        title.style.fontSize = "14px";
        if (title.scrollWidth > title.clientWidth) {
          title.style.fontSize = "13px";
        }
      }
    });
  }

  reference.initTaskCards = function (root) {
    if (instances.has(root)) return instances.get(root);
    var dialog = element("dialog", "modal-dialog task-card-dialog");
    do { dialog.id = "task-card-details-" + (++sequence); } while (document.getElementById(dialog.id));
    var heading = element("div", "modal-header");
    var titleGroup = element("div", "task-card-dialog-heading");
    var context = element("p", "task-card-dialog-context");
    var title = element("h2", "modal-title");
    title.id = dialog.id + "-title";
    dialog.setAttribute("aria-labelledby", title.id);
    var close = element("button", "btn-icon");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.title = "Close";
    close.innerHTML = '<svg class="icon" aria-hidden="true" focusable="false"><use href="#lucide-x"></use></svg>';
    titleGroup.append(context, title);
    heading.append(titleGroup, close);
    var summary = element("div", "task-card-dialog-summary");
    var body = element("div", "task-card-dialog-body");
    dialog.append(heading, summary, body);
    root.appendChild(dialog);
    var trigger = null;
    var tones = { neutral: "nominal", caution: "caution", danger: "danger", good: "good" };

    function open(event) {
      var card = event.target.closest("button[data-task-card]");
      if (!card || !root.contains(card) || card.disabled || dialog.open) return;
      trigger = card;
      card.setAttribute("aria-controls", dialog.id);
      title.textContent = card.querySelector(".task-card-title").textContent;
      var idNode = card.querySelector(".task-card-id");
      var projectNode = card.querySelector(".task-card-project");
      context.replaceChildren();
      if (idNode) context.appendChild(idNode.cloneNode(true));
      if (projectNode) context.appendChild(document.createTextNode(projectNode.textContent));
      var stateNode = card.querySelector(".task-card-state");
      var stateLabel = (stateNode && stateNode.textContent.trim()) || card.dataset.state || "";
      var status = element("span", "badge badge-" + (tones[card.dataset.tone] || "nominal"), stateLabel || (tones[card.dataset.tone] || "nominal"));
      summary.replaceChildren(status);
      var metadata = card.querySelector(".task-card-meta");
      if (metadata) {
        var copy = metadata.cloneNode(true);
        copy.querySelectorAll("time[datetime]").forEach(function (time) { time.textContent = time.dateTime; });
        summary.appendChild(copy);
      }
      var template = card.parentElement.querySelector("template[data-task-details]");
      body.replaceChildren();
      if (template) body.appendChild(template.content.cloneNode(true));
      dialog.showModal();
      close.focus();
    }

    adjustStreamlinedTitles(root);
    var resizeObserver = null;
    var onResize = null;
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(function () { adjustStreamlinedTitles(root); });
      resizeObserver.observe(root);
    } else if (typeof window !== "undefined") {
      onResize = function () { adjustStreamlinedTitles(root); };
      window.addEventListener("resize", onResize);
    }

    root.addEventListener("click", open);
    close.addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) {
      var rect = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
    dialog.addEventListener("keydown", function (event) {
      if (event.key !== "Tab") return;
      var controls = Array.from(dialog.querySelectorAll("button, a[href], input, select, textarea, [tabindex]")).filter(function (control) {
        return !control.disabled && control.tabIndex >= 0 && control.getClientRects().length;
      });
      var first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    dialog.addEventListener("close", function () {
      if (!dialog.open && trigger && trigger.isConnected) trigger.focus({ preventScroll: true });
    });
    var instance = {
      adjustTitles: function () { adjustStreamlinedTitles(root); },
      destroy: function () {
        if (resizeObserver) resizeObserver.disconnect();
        else if (onResize && typeof window !== "undefined") window.removeEventListener("resize", onResize);
        root.removeEventListener("click", open);
        if (dialog.open) dialog.close();
        root.querySelectorAll("[aria-controls]").forEach(function (control) {
          if (control.getAttribute("aria-controls") === dialog.id) control.removeAttribute("aria-controls");
        });
        dialog.remove();
        instances.delete(root);
      }
    };
    instances.set(root, instance);
    return instance;
  };

  document.querySelectorAll("[data-task-cards]").forEach(reference.initTaskCards);
}(window.ComponentReference = window.ComponentReference || {}));
