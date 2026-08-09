(function () {
  const PARTICLE_KEY = "jan26_show_dashboard_particles";

  function armParticlesOnLogin() {
    const form = document.getElementById("login-form");
    if (!form) return;
    form.addEventListener("submit", function () {
      try {
        sessionStorage.setItem(PARTICLE_KEY, "1");
      } catch (e) {
      }
    });
  }

  function makeParticle() {
    const el = document.createElement("span");
    const colors = ["#ff8f1f", "#ffffff", "#0a7005", "#000080"];
    const color = colors[Math.floor(Math.random() * colors.length)];
    const size = 6 + Math.random() * 8;
    const duration = 1.8 + Math.random() * 1.8;
    const drift = Math.round((Math.random() - 0.5) * 180);

    el.className = "jan26-particle";
    el.style.left = `${Math.random() * 100}%`;
    el.style.width = `${size}px`;
    el.style.height = `${size}px`;
    el.style.background = color;
    el.style.animationDuration = `${duration}s`;
    el.style.setProperty("--jan26-drift", `${drift}px`);
    el.style.animationDelay = `${Math.random() * 0.45}s`;
    return el;
  }

  function runDashboardParticles() {
    let shouldRun = false;
    try {
      shouldRun = sessionStorage.getItem(PARTICLE_KEY) === "1";
      sessionStorage.removeItem(PARTICLE_KEY);
    } catch (e) {
    }
    if (!shouldRun || document.getElementById("login-form")) return;

    const popup = document.createElement("div");
    popup.className = "jan26-popup";
    popup.innerHTML = [
      '<div class="jan26-popup-flag" aria-hidden="true"></div>',
      '<div class="jan26-popup-title">Happy Independence Day</div>',
      '<div class="jan26-popup-sub">Dr Bhasin&apos;s Lab</div>'
    ].join("");
    document.body.appendChild(popup);

    const wrap = document.createElement("div");
    wrap.className = "jan26-particle-wrap";
    wrap.setAttribute("aria-hidden", "true");
    document.body.appendChild(wrap);

    const timer = window.setInterval(function () {
      for (let i = 0; i < 12; i += 1) {
        const particle = makeParticle();
        wrap.appendChild(particle);
        window.setTimeout(function () {
          particle.remove();
        }, 4200);
      }
    }, 140);

    window.setTimeout(function () {
      window.clearInterval(timer);
      popup.classList.add("is-leaving");
      window.setTimeout(function () {
        popup.remove();
      }, 450);
      window.setTimeout(function () {
        wrap.remove();
      }, 4300);
    }, 4000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      armParticlesOnLogin();
      runDashboardParticles();
    });
  } else {
    armParticlesOnLogin();
    runDashboardParticles();
  }
})();
