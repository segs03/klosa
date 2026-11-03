(() => {
    const banner = document.getElementById("installBanner");
    const installBtn = document.getElementById("installBtn");
    const dismissBtn = document.getElementById("dismissInstall");

    if (!banner || !installBtn || !dismissBtn) return;

    let deferredPrompt = null;

    // Don't show the banner if already installed
    const isInstalled =
        window.matchMedia("(display-mode: standalone)").matches ||
        window.navigator.standalone === true;

    if (isInstalled) return;

    // Show banner when installation is available
    window.addEventListener("beforeinstallprompt", event => {
        event.preventDefault();

        deferredPrompt = event;
        banner.hidden = false;
    });

    // Install
    installBtn.addEventListener("click", async () => {
        if (!deferredPrompt) return;

        deferredPrompt.prompt();

        const choice = await deferredPrompt.userChoice;

        if (choice.outcome === "accepted") {
            banner.hidden = true;
        }

        deferredPrompt = null;
    });

    // Dismiss
    dismissBtn.addEventListener("click", () => {
        banner.hidden = true;
    });

    // Installation completed
    window.addEventListener("appinstalled", () => {
        banner.hidden = true;
        deferredPrompt = null;
    });
})();