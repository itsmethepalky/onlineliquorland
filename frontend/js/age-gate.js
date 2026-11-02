(() => {
  const consentKey = "oll-age-consent";
  const denialKey = "oll-age-denied";
  let savedConsent = "";

  try {
    savedConsent = localStorage.getItem(consentKey) || "";
    if (savedConsent !== "adult" && sessionStorage.getItem(denialKey)) {
      savedConsent = "denied";
    }
  } catch (error) {
    console.warn("Age confirmation could not be read from browser storage.");
  }

  if (savedConsent === "adult") {
    return;
  }

  document.documentElement.classList.add("age-gate-required");

  function renderAgeGate() {
    if (!document.body || document.querySelector(".age-gate-overlay")) {
      return;
    }

    const overlay = document.createElement("div");
    overlay.className = "age-gate-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-labelledby", "age-gate-title");
    overlay.setAttribute("aria-describedby", "age-gate-description");
    overlay.tabIndex = -1;

    const panel = document.createElement("section");
    panel.className = "age-gate-panel";

    const label = document.createElement("p");
    label.className = "age-gate-eyebrow";
    label.textContent = "ONLINE LIQUOR LAND";

    const title = document.createElement("h1");
    title.id = "age-gate-title";
    title.textContent = "Before you enter";

    const description = document.createElement("p");
    description.id = "age-gate-description";
    description.textContent =
      "Are you 18 years of age or older? This store is intended only for adults who may legally purchase alcohol in Nepal.";

    const notice = document.createElement("p");
    notice.className = "age-gate-notice";
    notice.textContent =
      "By choosing Yes, you confirm that you meet the legal age requirement. This confirmation is not proof of age.";

    const actions = document.createElement("div");
    actions.className = "age-gate-actions";

    function showDeniedMessage() {
      try {
        sessionStorage.setItem(denialKey, "yes");
      } catch (error) {
        console.warn("Age refusal could not be saved in browser storage.");
      }

      title.textContent = "You cannot enter";
      description.textContent =
        "This store is only for adults who meet the legal age requirement to purchase alcohol in Nepal.";
      notice.textContent = "Please close this tab to leave the website.";
      actions.remove();
      overlay.focus();
    }

    const yesButton = document.createElement("button");
    yesButton.type = "button";
    yesButton.className = "age-gate-button age-gate-yes";
    yesButton.textContent = "Yes, I am 18 or older";
    yesButton.addEventListener("click", () => {
      try {
        localStorage.setItem(consentKey, "adult");
        sessionStorage.removeItem(denialKey);
      } catch (error) {
        console.warn("Age confirmation could not be saved in browser storage.");
      }

      document.documentElement.classList.remove("age-gate-required");
      overlay.remove();
    });

    const noButton = document.createElement("button");
    noButton.type = "button";
    noButton.className = "age-gate-button age-gate-no";
    noButton.textContent = "No, I am under 18";
    noButton.addEventListener("click", showDeniedMessage);

    if (savedConsent === "denied") {
      showDeniedMessage();
    } else {
      actions.append(yesButton, noButton);
    }
    panel.append(label, title, description, notice);
    if (actions.childElementCount) {
      panel.append(actions);
    }
    overlay.append(panel);
    document.body.append(overlay);
    overlay.focus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderAgeGate, { once: true });
  } else {
    renderAgeGate();
  }
})();
