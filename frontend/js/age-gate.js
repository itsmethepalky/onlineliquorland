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

  const ageGateStyles = document.createElement("style");
  ageGateStyles.textContent = `
    html.age-gate-required,
    html.age-gate-required body {
      overflow: hidden !important;
    }

    html.age-gate-required body > :not(.age-gate-overlay) {
      visibility: hidden !important;
    }

    .age-gate-overlay {
      position: fixed !important;
      z-index: 2147483000 !important;
      inset: 0 !important;
      display: grid !important;
      place-items: center !important;
      overflow-y: auto !important;
      padding: 20px !important;
      background: rgba(25, 18, 19, 0.88) !important;
      color: #211a1b !important;
      visibility: visible !important;
    }

    .age-gate-panel {
      width: min(100%, 520px) !important;
      padding: clamp(24px, 6vw, 42px) !important;
      border: 1px solid #e5dbcc !important;
      border-radius: 18px !important;
      background: #fbf8f1 !important;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.3) !important;
      text-align: center !important;
    }

    .age-gate-eyebrow {
      margin: 0 0 12px !important;
      color: #4e202a !important;
      font-size: 0.75rem !important;
      font-weight: 800 !important;
      letter-spacing: 0.15em !important;
    }

    .age-gate-panel h1 {
      margin: 0 0 14px !important;
      font-family: Georgia, "Times New Roman", serif !important;
      font-size: clamp(1.8rem, 6vw, 2.4rem) !important;
      line-height: 1.18 !important;
    }

    .age-gate-panel p {
      line-height: 1.65 !important;
    }

    .age-gate-notice {
      color: #675c59 !important;
      font-size: 0.88rem !important;
    }

    .age-gate-actions {
      display: grid !important;
      grid-template-columns: 1fr 1fr !important;
      gap: 12px !important;
      margin-top: 24px !important;
    }

    .age-gate-button {
      min-height: 48px !important;
      border: 1px solid #4e202a !important;
      border-radius: 12px !important;
      padding: 10px 16px !important;
      font: inherit !important;
      font-weight: 750 !important;
      cursor: pointer !important;
    }

    .age-gate-yes {
      background: #4e202a !important;
      color: #fff !important;
    }

    .age-gate-no {
      background: transparent !important;
      color: #211a1b !important;
    }

    .age-gate-button:focus-visible {
      outline: 3px solid #b88a49 !important;
      outline-offset: 3px !important;
    }

    @media (max-width: 480px) {
      .age-gate-actions {
        grid-template-columns: 1fr !important;
      }
    }
  `;
  document.head.append(ageGateStyles);
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
