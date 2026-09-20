(function () {
    const button = document.getElementById("shareReceiptPdfBtn");
    if (!button) return;

    const label = button.querySelector("[data-share-label]");
    const originalLabel = label ? label.textContent : "Partager PDF";
    const originalHtml = button.innerHTML;

    function setBusy(isBusy) {
        button.disabled = isBusy;
        if (isBusy) {
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Préparation...';
        } else {
            button.innerHTML = originalHtml;
        }
    }

    function notify(message, type) {
        const alert = document.createElement("div");
        alert.className = `alert alert-${type || "info"} receipt-share-alert shadow-sm`;
        alert.setAttribute("role", "status");
        alert.textContent = message;
        button.closest(".receipt-actions").insertAdjacentElement("afterend", alert);
        window.setTimeout(() => alert.remove(), 6500);
    }

    function downloadBlob(blob, fileName) {
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = fileName;
        link.style.display = "none";
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1500);
    }

    async function fetchPdf(pdfUrl) {
        const response = await fetch(pdfUrl, {
            method: "GET",
            credentials: "same-origin",
            headers: { "X-Requested-With": "XMLHttpRequest" }
        });

        if (!response.ok) {
            throw new Error("pdf_response_not_ok");
        }

        const contentType = response.headers.get("Content-Type") || "";
        const blob = await response.blob();
        if (!contentType.includes("application/pdf") && blob.type !== "application/pdf") {
            throw new Error("pdf_invalid_type");
        }
        return blob;
    }

    button.addEventListener("click", async function () {
        const pdfUrl = button.dataset.pdfUrl;
        const fileName = button.dataset.fileName || "recu-paiement.pdf";
        const title = button.dataset.shareTitle || "Reçu de paiement";
        const text = button.dataset.shareText || "Bonjour, voici votre reçu de paiement KLASORA.";
        const whatsappUrl = button.dataset.whatsappUrl || "https://wa.me/?text=Bonjour%2C%20voici%20votre%20re%C3%A7u%20de%20paiement%20KLASORA.";

        setBusy(true);
        try {
            const blob = await fetchPdf(pdfUrl);
            const file = new File([blob], fileName, { type: "application/pdf" });

            if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
                try {
                    await navigator.share({ files: [file], title, text });
                    return;
                } catch (error) {
                    if (error && error.name === "AbortError") {
                        return;
                    }
                    throw error;
                }
            }

            downloadBlob(blob, fileName);
            notify("PDF téléchargé. Joignez-le dans WhatsApp.", "info");
            window.setTimeout(() => {
                window.open(whatsappUrl, "_blank", "noopener");
            }, 400);
        } catch (error) {
            console.error("[Receipt PDF share]", error);
            notify("Impossible de préparer le PDF.", "danger");
        } finally {
            setBusy(false);
        }
    });
})();
