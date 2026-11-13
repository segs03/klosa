
(() => {
    "use strict";

    function initTransfer() {
        const form = document.getElementById("sendForm");

        // Stop if this is not the Send Money page.
        if (!form) return;

        // Form elements
        const amountInput = document.getElementById("id_amount");
        const accountNumber = document.getElementById("id_account_number");
        const beneficiary = document.getElementById("id_beneficiary");
        const pin = document.getElementById("id_pin");

        // Dialog elements
        const sheet = document.getElementById("confirmSheet");
        const openBtn = document.getElementById("openConfirm");
        const cancelBtn = document.getElementById("cancelConfirm");
        const sendBtn = document.getElementById("sendBtn");

        // Error and summary elements
        const errBen = document.getElementById("errBen");
        const errAmt = document.getElementById("errAmt");
        const errPin = document.getElementById("errPin");
        const sumTo = document.getElementById("sumTo");
        const sumAmt = document.getElementById("sumAmt");

        const requiredElements = [
            amountInput,
            accountNumber,
            beneficiary,
            pin,
            sheet,
            openBtn,
            cancelBtn,
            sendBtn,
            errBen,
            errAmt,
            errPin,
            sumTo,
            sumAmt
        ];

        if (requiredElements.some(element => !element)) {
            console.error(
                "Send page initialization failed: a required HTML element is missing.",
                requiredElements
            );
            return;
        }

        // Account balance
        const balance = Number(form.dataset.balance);

        if (!Number.isFinite(balance)) {
            console.error("Invalid account balance.");
            return;
        }

        // Currency formatter
        const naira = amount =>
            "₦" + amount.toLocaleString("en-NG", {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            });

        // Quick amount buttons
        const quickButtons = Array.from(
            form.querySelectorAll("[data-amount]")
        );

        quickButtons.forEach(button => {
            button.addEventListener("click", () => {
                amountInput.value = Number(
                    button.dataset.amount
                ).toFixed(2);

                quickButtons.forEach(item => {
                    item.classList.remove("active");
                });

                button.classList.add("active");

                errAmt.textContent = "";
            });
        });

        // Remove active quick amount when manually editing.
        amountInput.addEventListener("input", () => {
            quickButtons.forEach(button => {
                button.classList.remove("active");
            });

            errAmt.textContent = "";
        });

        // Clear recipient errors when changing recipient.
        accountNumber.addEventListener("input", () => {
            errBen.textContent = "";
        });

        beneficiary.addEventListener("change", () => {
            errBen.textContent = "";
        });

        // Validate transfer details.
        function validateTransfer() {
            let valid = true;

            errBen.textContent = "";
            errAmt.textContent = "";

            const account = accountNumber.value.trim();
            const selectedBeneficiary = beneficiary.value;
            const amount = Number(amountInput.value);

            if (!account && !selectedBeneficiary) {
                errBen.textContent =
                    "Enter an account number or select a beneficiary.";

                valid = false;
            }

            if (account && selectedBeneficiary) {
                errBen.textContent =
                    "Choose only one recipient method.";

                valid = false;
            }

            if (
                amountInput.value.trim() === "" ||
                !Number.isFinite(amount) ||
                amount <= 0
            ) {
                errAmt.textContent = "Enter a valid amount.";
                valid = false;
            } else if (amount > balance) {
                errAmt.textContent = "Insufficient balance.";
                valid = false;
            }

            return valid;
        }

        // Get recipient display name.
        function getRecipient() {
            if (accountNumber.value.trim()) {
                return accountNumber.value.trim();
            }

            return beneficiary.options[
                beneficiary.selectedIndex
            ]?.text || "";
        }

        // Open confirmation dialog.
        function openConfirmation() {
            if (!validateTransfer()) return;

            const amount = Number(amountInput.value);

            sumTo.textContent = getRecipient();
            sumAmt.textContent = naira(amount);

            pin.value = "";
            errPin.textContent = "";

            sendBtn.disabled = true;
            sendBtn.textContent = "Confirm and send";

            sheet.showModal();
            pin.focus();
        }

        openBtn.addEventListener("click", openConfirmation);

        // Cancel confirmation.
        cancelBtn.addEventListener("click", () => {
            sheet.close();
        });

        // PIN validation.
        pin.addEventListener("input", () => {
            pin.value = pin.value
                .replace(/\D/g, "")
                .slice(0, 4);

            errPin.textContent = "";

            sendBtn.disabled = pin.value.length !== 4;
        });

        // Prevent Enter from bypassing confirmation.
        form.addEventListener("keydown", event => {
            if (
                event.key === "Enter" &&
                !sheet.open &&
                event.target !== sendBtn
            ) {
                event.preventDefault();
                openConfirmation();
            }
        });

        // Submission protection.
        let submitting = false;

        form.addEventListener("submit", event => {
            if (submitting) {
                event.preventDefault();
                return;
            }

            if (pin.value.length !== 4) {
                event.preventDefault();
                errPin.textContent =
                    "Enter your 4-digit PIN.";
                return;
            }

            submitting = true;

            sendBtn.disabled = true;
            sendBtn.textContent = "Sending...";
        });

        // Reopen dialog after server-side PIN validation errors.
        if (form.dataset.reopen === "true") {
            sheet.showModal();
            pin.focus();
        }

        console.log("Send Money script initialized successfully.");
    }

    // Initialize after the HTML is ready.
    if (document.readyState === "loading") {
        document.addEventListener(
            "DOMContentLoaded",
            initTransfer,
            { once: true }
        );
    } else {
        initTransfer();
    }

})();
