(function () {
  function createLucideIcon(name, className) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    svg.setAttribute("class", className || "icon");
    svg.setAttribute("aria-hidden", "true");
    use.setAttribute("href", "#lucide-" + name);
    svg.appendChild(use);
    return svg;
  }

  window.ComponentReference = { createLucideIcon: createLucideIcon };
}());
