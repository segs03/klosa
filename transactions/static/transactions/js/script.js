
/** const panel = document.querySelector("#transaction-panel");
const handle = document.querySelector("#transaction-handle");

let startY = 0;
let currentY = 0;
let dragging = false;

handle.addEventListener("pointerdown", (event) => {
    startY = event.clientY;
    currentY = startY;
    dragging = true;

    handle.setPointerCapture(event.pointerId);
});

handle.addEventListener("pointermove", (event) => {
    if (!dragging) return;

    currentY = event.clientY;
});

handle.addEventListener("pointerup", () => {
    if (!dragging) return;

    const distance = startY - currentY;

    if (distance > 40) {
        panel.classList.add("expanded");
    } else if (distance < -40) {
        panel.classList.remove("expanded");
    } else {
        panel.classList.toggle("expanded");
    }

    dragging = false;
});

handle.addEventListener("pointercancel", () => {
    dragging = false;
});
*/

// Transaction filters

const filterButtons = document.querySelectorAll(".filter-btn");
const transactionItems = document.querySelectorAll(
    ".full-history .transaction-item"
);

filterButtons.forEach((button) => {
    button.addEventListener("click", () => {

        filterButtons.forEach((btn) => {
            btn.classList.remove("active");
        });

        button.classList.add("active");

        const filter = button.dataset.filter;

        transactionItems.forEach((item) => {
            const type = item.dataset.type;

            if (
                filter === "all" ||
                type === filter
            ) {
                item.style.display = "flex";
            } else {
                item.style.display = "none";
            }
        });
    });
});
