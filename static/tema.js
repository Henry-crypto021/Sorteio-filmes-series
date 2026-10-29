(function () {
  // Aplica o tema salvo (escuro por padrão) antes da página aparecer
  var tema = localStorage.getItem("tema") || "escuro";
  document.documentElement.setAttribute("data-tema", tema);

  document.addEventListener("DOMContentLoaded", function () {
    var botao = document.getElementById("tema-botao");
    if (!botao) return;

    botao.addEventListener("click", function () {
      var atual = document.documentElement.getAttribute("data-tema");
      var novo = atual === "claro" ? "escuro" : "claro";
      document.documentElement.setAttribute("data-tema", novo);
      localStorage.setItem("tema", novo);
    });
  });
})();
