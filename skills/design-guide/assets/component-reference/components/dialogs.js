(function (spawnToast) {
  // Modal dialog
  var dialog = document.getElementById("demoDialog");
  var openModalBtn = document.getElementById("btnOpenModal");
  var closeModalBtn = document.getElementById("btnCloseModal");
  var cancelModalBtn = document.getElementById("btnCancelModal");
  var confirmModalBtn = document.getElementById("btnConfirmModal");

  if (openModalBtn && dialog) {
    openModalBtn.addEventListener("click", function () {
      dialog.showModal();
    });
    closeModalBtn.addEventListener("click", function () { dialog.close(); });
    cancelModalBtn.addEventListener("click", function () { dialog.close(); });
    confirmModalBtn.addEventListener("click", function () {
      dialog.close();
      spawnToast("Traffic successfully redirected to stable cluster.");
    });
  }
}(window.ComponentReference.spawnToast));
