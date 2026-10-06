document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form[data-confirm]").forEach((form) => {
        form.addEventListener("submit", (event) => {
            if (!window.confirm(form.dataset.confirm)) {
                event.preventDefault();
            }
        });
    });

    document.querySelectorAll("form[data-loading-label]").forEach((form) => {
        form.addEventListener("submit", (event) => {
            if (!form.reportValidity()) {
                event.preventDefault();
                return;
            }
            const submitter = event.submitter;
            if (submitter) {
                submitter.dataset.originalLabel = submitter.textContent;
                submitter.textContent = form.dataset.loadingLabel;
                submitter.disabled = true;
            }
            form.setAttribute("aria-busy", "true");
        });
    });

    const roomChangeForm = document.querySelector(".room-change-form");
    if (roomChangeForm) {
        const roomSummary = document.querySelector("#summary-room");
        const reasonSummary = document.querySelector("#summary-reason");
        const descriptionSummary = document.querySelector("#summary-description");
        const updateSummary = () => {
            const selectedRoom = roomChangeForm.querySelector(
                'input[name="requested_room_id"]:checked'
            );
            const selectedReason = roomChangeForm.querySelector(
                'select[name="reason"]'
            );
            const description = roomChangeForm.querySelector(
                'textarea[name="description"]'
            );
            roomSummary.textContent = selectedRoom
                ? `${selectedRoom.dataset.roomName} · ${selectedRoom.dataset.roomType}`
                : "Select one available room";
            reasonSummary.textContent = selectedReason.value || "Select a reason";
            descriptionSummary.textContent =
                description.value.trim() || "Enter your detailed description";
            roomChangeForm.querySelectorAll(".room-option").forEach((card) => {
                card.classList.toggle(
                    "selected",
                    Boolean(card.querySelector("input").checked)
                );
            });
        };
        roomChangeForm.addEventListener("change", updateSummary);
        roomChangeForm.addEventListener("input", updateSummary);
        updateSummary();
    }

    document.querySelectorAll(".flash").forEach((message) => {
        window.setTimeout(() => {
            message.classList.add("flash-hide");
            window.setTimeout(() => message.remove(), 250);
        }, 6000);
    });
});
