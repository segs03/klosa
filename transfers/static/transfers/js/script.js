window.onerror = function(message, source, line, column) {
    alert(
        "JS Error: " + message +
        "\nLine: " + line +
        "\nColumn: " + column
    );
};


(() => {
    function initTransfer() {

        const form = document.getElementById("sendForm");
        const amountInput = document.getElementById("id_amount");
        const accountNumber = document.getElementById("id_account_number");
        const ben = document.getElementById("id_beneficiary");
        const pin = document.getElementById("id_pin");

        const sheet = document.getElementById("confirmSheet");
        const openBtn = document.getElementById("openConfirm");
        const cancelBtn = document.getElementById("cancelConfirm");
        const sendBtn = document.getElementById("sendBtn");

        if (!form || !amountInput || !pin || !sheet || !openBtn) {
            console.error("Missing transfer form element.");
            return;
        }

        const balance = parseFloat(form.dataset.balance);

        const naira = (n) =>
            "₦" + n.toLocaleString("en-NG", {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            });

        // Quick amount buttons
        const quickButtons = document.querySelectorAll("[data-amount]");

        quickButtons.forEach(button => {
            button.addEventListener("click", () => {
                amountInput.value = Number(
                    button.dataset.amount
                ).toFixed(2);

                quickButtons.forEach(item => {
                    item.classList.remove("active");
                });

                button.classList.add("active");
            });
        });

        amountInput.addEventListener("input", () => {
            quickButtons.forEach(button => {
                button.classList.remove("active");
            });
        });

        // Validation
        function validate() {
            let ok = true;

            const errBen = document.getElementById("errBen");
            const errAmt = document.getElementById("errAmt");

            errBen.textContent = "";
            errAmt.textContent = "";

            const account = accountNumber.value.trim();
            const beneficiary = ben.value;
            const amount = parseFloat(amountInput.value);

            if (!account && !beneficiary) {
                errBen.textContent =
                    "Enter an account number or select a beneficiary.";
                ok = false;
            }

            if (account && beneficiary) {
                errBen.textContent =
                    "Choose only one recipient method.";
                ok = false;
            }

            if (!amountInput.value || isNaN(amount) || amount <= 0) {
                errAmt.textContent = "Enter a valid amount.";
                ok = false;
            } else if (amount > balance) {
                errAmt.textContent = "Insufficient balance.";
                ok = false;
            }

            return ok;
        }

        // Open confirmation dialog
        function openSheet() {

            if (!validate()) return;

            const recipient = accountNumber.value.trim()
                ? accountNumber.value.trim()
                : ben.options[ben.selectedIndex].text;

            document.getElementById("sumTo").textContent = recipient;

            document.getElementById("sumAmt").textContent =
                naira(parseFloat(amountInput.value));

            pin.value = "";
            sendBtn.disabled = true;

            sheet.showModal();
            pin.focus();
        }

        // Continue button
        openBtn.addEventListener("click", () => {
            console.log("Continue button clicked");
            openSheet();
        });

        // Cancel button
        cancelBtn?.addEventListener("click", () => {
            sheet.close();
        });

        // PIN validation
        pin.addEventListener("input", () => {
            pin.value = pin.value.replace(/\D/g, "").slice(0, 4);

            sendBtn.disabled = pin.value.length !== 4;
        });

        // Enter opens confirmation instead of submitting
        form.addEventListener("keydown", e => {
            if (e.key === "Enter" && !sheet.open) {
                e.preventDefault();
                openSheet();
            }
        });

        // Prevent repeated submission
        form.addEventListener("submit", () => {
            sendBtn.disabled = true;
            sendBtn.textContent = "Sending...";
        });

        // Reopen dialog after server-side validation error
        if (form.dataset.reopen === "true") {
            sheet.showModal();
            pin.focus();
        }

        console.log("Transfer script initialized successfully.");
    }

    // Works whether script loads before or after DOMContentLoaded
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initTransfer);
    } else {
        initTransfer();
    }

})();