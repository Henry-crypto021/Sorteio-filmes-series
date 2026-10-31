// Roleta de suspense: pôsteres passam, desaceleram e param no título sorteado
(function () {
  const reduzir = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function abrirPagina(html) {
    document.open();
    document.write(html);
    document.close();
  }

  function esperarImagens(imgs, limite) {
    const todas = Promise.all(imgs.map(function (img) {
      return new Promise(function (ok) {
        if (img.complete) return ok();
        img.addEventListener("load", ok);
        img.addEventListener("error", ok);
      });
    }));
    return Promise.race([todas, new Promise(function (ok) { setTimeout(ok, limite); })]);
  }

  function confete(tela) {
    const cores = ["#ff4b5c", "#ffb347", "#47e891", "#4facfe", "#b06ab3", "#ffffff"];
    const caixa = tela.querySelector(".rol-janela").getBoundingClientRect();
    const cx = caixa.left + caixa.width / 2;
    const cy = caixa.top + caixa.height / 2;
    for (let i = 0; i < 50; i++) {
      const p = document.createElement("span");
      p.className = "rol-confete";
      p.style.left = cx + "px";
      p.style.top = cy + "px";
      p.style.background = cores[i % cores.length];
      const angulo = Math.random() * Math.PI * 2;
      const distancia = 120 + Math.random() * 220;
      p.style.setProperty("--dx", Math.cos(angulo) * distancia + "px");
      p.style.setProperty("--dy", Math.sin(angulo) * distancia - 60 + "px");
      p.style.setProperty("--giro", (Math.random() * 720 - 360) + "deg");
      tela.appendChild(p);
    }
  }

  document.addEventListener("submit", async function (e) {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    const acao = form.getAttribute("action");
    if (acao !== "/resultado" && acao !== "/quero-assistir/sortear" && acao !== "/grupo") return;
    // Só os botões "Sortear" e "Sortear outro" (os pôsteres de "Mais para você" não)
    if (!e.submitter || !e.submitter.classList.contains("botao-principal")) return;

    e.preventDefault();
    if (document.querySelector(".rol-tela")) return;

    const tela = document.createElement("div");
    tela.className = "rol-tela";
    tela.innerHTML =
      '<div class="rol-caixa">' +
        '<p class="rol-titulo">Sorteando<span class="pontos"></span></p>' +
        '<div class="rol-janela"><div class="rol-marca"></div><div class="rol-trilho"></div></div>' +
      '</div>';
    document.body.appendChild(tela);

    // Pede o sorteio ao servidor
    let html;
    try {
  const resposta = await fetch(acao, { method: "POST", body: new FormData(form) });
      if (!resposta.ok) throw new Error("falhou");
      html = await resposta.text();
    } catch (erro) {
      tela.remove();
      form.submit(); // plano B: envia do jeito normal
      return;
    }

    // Pega o pôster sorteado e os outros da lista
    const doc = new DOMParser().parseFromString(html, "text/html");
    const imgVencedor = doc.querySelector(".card > img");
    const vencedor = imgVencedor ? imgVencedor.getAttribute("src") : null;
    const pool = Array.from(doc.querySelectorAll(".mini img, #roleta-pool img")).map(function (i) {
      return i.getAttribute("src");
    });

    // Sem pôster ou com "menos movimento" ligado: vai direto pro resultado
    if (!vencedor || reduzir) {
      abrirPagina(html);
      return;
    }
        // Baixa todos os pôsteres antes de mostrar a faixa (evita pôster preto)
    const urls = Array.from(new Set(pool.concat([vencedor])));
    await esperarImagens(urls.map(function (u) {
      const pre = new Image();
      pre.src = u;
      return pre;
    }), 3000);

    // Monta a faixa: o sorteado fica numa posição fixa perto do fim
    const TOTAL = 32;
    const POSICAO = 26;
    const fontes = pool.length ? pool : [vencedor];
    const trilho = tela.querySelector(".rol-trilho");
    const imgs = [];
    let ultimo = "";

    for (let i = 0; i < TOTAL; i++) {
      let src;
      if (i === POSICAO) {
        src = vencedor;
      } else {
        do {
          src = fontes[Math.floor(Math.random() * fontes.length)];
        } while (fontes.length > 1 && src === ultimo);
      }
      ultimo = src;

      const el = document.createElement("img");
      el.className = "rol-poster";
      el.alt = "";
      el.src = src;
      trilho.appendChild(el);
      imgs.push(el);
    }

    await esperarImagens(imgs, 2000);
    tela.querySelector(".rol-janela").classList.add("rol-pronta");

    // Calcula quanto a faixa precisa andar para o sorteado parar no centro
    const janela = tela.querySelector(".rol-janela");
    const alvo = imgs[POSICAO];
    const deslocamento = janela.clientWidth / 2 - (alvo.offsetLeft + alvo.offsetWidth / 2);

    void trilho.offsetWidth; // força o navegador a registrar a posição inicial
    trilho.style.transition = "transform 5s cubic-bezier(0.12, 0.65, 0.1, 1)";
    trilho.style.transform = "translateX(" + deslocamento + "px)";

       let terminou = false;
    function finalizar() {
      if (terminou) return;
      terminou = true;
      alvo.classList.add("rol-vencedor");
      tela.querySelector(".rol-titulo").textContent = "Sorteado!";
      confete(tela);
      setTimeout(function () { abrirPagina(html); }, 1500);
    }

    trilho.addEventListener("transitionend", function (ev) {
      if (ev.target === trilho) finalizar();
    });
    setTimeout(finalizar, 5600); // garantia, caso o evento não dispare
  });
})();