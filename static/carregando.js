// Tela de "Carregando..." ao abrir um título ou fazer uma busca
(function () {
  function mostrar() {
    if (document.querySelector(".carregando")) return;
    const tela = document.createElement("div");
    tela.className = "carregando";
    tela.innerHTML = '<div class="carregando-giro"></div><p>Carregando<span class="pontos"></span></p>';
    document.body.appendChild(tela);
  }

  // Voltar com o botão do navegador não pode deixar a tela de carregamento
  window.addEventListener("pageshow", function () {
    const tela = document.querySelector(".carregando");
    if (tela) tela.remove();
  });

  document.addEventListener("click", function (e) {
    const link = e.target.closest("a[href]");
    if (!link || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (link.target === "_blank") return;
    if (/^\/(titulo|avaliar)\//.test(link.getAttribute("href"))) mostrar();
  });

  document.addEventListener("submit", function (e) {
    const acao = e.target.getAttribute("action");
    const botao = e.submitter;
    if (acao === "/buscar" || (acao === "/resultado" && botao && botao.classList.contains("mini"))) {
      mostrar();
    }
  });
})();