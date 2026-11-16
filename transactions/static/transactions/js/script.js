// Transaction filters
(() => {
    const panel = document.getElementById("transaction-panel");
    if (!panel) return;

    const buttons = panel.querySelectorAll(".filter-btn");
    const items = panel.querySelectorAll(".full-history .transaction-item");
    const none = panel.querySelector("#noMatch");

    buttons.forEach((button) => {
        button.addEventListener("click", () => {
            buttons.forEach((b) => {
                b.classList.remove("active");
                b.setAttribute("aria-pressed", "false");
            });
            button.classList.add("active");
            button.setAttribute("aria-pressed", "true");

            const filter = button.dataset.filter;
            let shown = 0;

            items.forEach((item) => {
                const show = filter === "all" || item.dataset.type === filter;
                item.style.display = show ? "" : "none";
                if (show) shown++;
            });

            if (none) none.hidden = shown > 0 || items.length === 0;
        });
    });
})();