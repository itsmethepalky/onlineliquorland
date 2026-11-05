(function () {
  "use strict";

  function initializeAdminSidebar() {
    const header = document.querySelector(
      "body.admin-layout .store-header"
    );
    const navigation = header?.querySelector(".admin-nav");

    if (!header || !navigation) {
      return;
    }

    const backdrop = document.createElement("button");
    backdrop.type = "button";
    backdrop.className = "admin-sidebar-backdrop";
    backdrop.setAttribute("aria-label", "Close admin navigation");
    backdrop.setAttribute("aria-hidden", "true");

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "admin-sidebar-toggle";
    toggle.setAttribute("aria-controls", "adminNavigation");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open admin navigation");
    toggle.innerHTML =
      '<i class="bi bi-list" aria-hidden="true"></i>' +
      '<span>Menu</span>';

    navigation.id = "adminNavigation";
    document.body.append(backdrop, toggle);

    function setOpen(open) {
      document.body.classList.toggle("admin-sidebar-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute(
        "aria-label",
        open ? "Close admin navigation" : "Open admin navigation"
      );
      backdrop.setAttribute("aria-hidden", String(!open));

      const icon = toggle.querySelector("i");
      if (icon) {
        icon.className = open ? "bi bi-x-lg" : "bi bi-list";
      }

      if (window.matchMedia("(max-width: 1100px)").matches) {
        document.body.style.overflow = open ? "hidden" : "";
      }
    }

    toggle.addEventListener("click", function () {
      setOpen(!document.body.classList.contains("admin-sidebar-open"));
    });

    backdrop.addEventListener("click", function () {
      setOpen(false);
    });

    navigation.addEventListener("click", function (event) {
      if (event.target.closest("a")) {
        setOpen(false);
      }
    });

    document.addEventListener("keydown", function (event) {
      if (
        event.key === "Escape" &&
        document.body.classList.contains("admin-sidebar-open")
      ) {
        setOpen(false);
        toggle.focus();
      }
    });

    window.matchMedia("(min-width: 1101px)")
      .addEventListener("change", function (event) {
        if (event.matches) {
          setOpen(false);
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      initializeAdminSidebar,
      { once: true }
    );
  } else {
    initializeAdminSidebar();
  }
})();
