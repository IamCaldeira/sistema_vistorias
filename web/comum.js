/* Funções comuns ao painel (PC) e à tela do celular */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const STATUS = {pendente: 'Pendente', em_andamento: 'Em andamento', concluido: 'Concluído'};
const ALERTAS = {
  nao_localizado: ['fa-circle-question', 'Não localizado'],
  campo: ['fa-circle-plus', 'Cadastrado em campo (não constava na lista)'],
  sem_gps: ['fa-location-crosshairs', 'Sem GPS'],
  gps_impreciso: ['fa-location-dot', 'GPS impreciso'],
  puladas: ['fa-forward', 'Fotos puladas'],
  avarias: ['fa-car-burst', 'Com avarias registradas'],
  placa_ilegivel: ['fa-eye-slash', 'Placa ilegível'],
  itens_faltando: ['fa-screwdriver-wrench', 'Item de segurança ausente (estepe, triângulo, macaco ou chave de roda)'],
};
const badge = s => `<span class="badge b-${s}">${STATUS[s] || s}</span>`;
const icones = (lista) => (lista || []).map(a => ALERTAS[a] ? `<i class="fa-solid ${ALERTAS[a][0]} alerta a-${a}" title="${ALERTAS[a][1]}"></i>` : '').join('');

async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (r.status === 401) { const e = new Error('Sessão expirada'); e.sessao = true; throw e; }
  const t = await r.text();
  let j; try { j = JSON.parse(t); } catch (e) { throw new Error('Resposta inválida do servidor'); }
  if (j.ok === false) throw new Error(j.erro || 'Erro');
  return j;
}
const post = (url, dados) => api(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados || {})});

const usuarioSalvo = () => { try { return JSON.parse(localStorage.getItem('usuario')); } catch (e) { return null; } };

/* Login (matrícula + senha). Na primeira vez, cria o administrador.
   Sem conexão com o notebook, reaproveita o último usuário (modo offline do celular). */
function iniciarSessao(aoEntrar) {
  let tela = $('#login');
  if (!tela) {
    tela = document.createElement('div'); tela.id = 'login';
    tela.innerHTML = `<div class="caixa-login"><img src="/web/brasao.png" alt="">
      <h2>Levantamento da Frota</h2><small>Prefeitura Municipal de Caratinga<br>Departamento de Patrimônio</small>
      <form id="lg-login"><label>Matrícula</label><input type="text" id="lg-mat" autocomplete="username" placeholder="com ou sem pontos e hífen">
        <label>Senha</label><input type="password" id="lg-senha" autocomplete="current-password">
        <div class="erro" id="lg-erro"></div>
        <button class="btn" type="submit"><i class="fa-solid fa-right-to-bracket"></i> Entrar</button></form>
      <form id="lg-setup" style="display:none"><div class="aviso">Primeiro uso do sistema: cadastre o
        <b>administrador</b>. Ele cria os demais usuários depois.</div>
        <label>Nome completo</label><input type="text" id="st-nome" autocomplete="name">
        <label>Matrícula</label><input type="text" id="st-mat" autocomplete="username">
        <label>Senha (mínimo 4 caracteres)</label><input type="password" id="st-senha" autocomplete="new-password">
        <label>Repita a senha</label><input type="password" id="st-senha2" autocomplete="new-password">
        <div class="erro" id="st-erro"></div>
        <button class="btn" type="submit"><i class="fa-solid fa-user-shield"></i> Criar administrador</button></form></div>`;
    document.body.appendChild(tela);
  }
  const entrar = (u, info) => {
    try { localStorage.setItem('usuario', JSON.stringify(u)); } catch (e) {}
    document.body.classList.add('perfil-' + (u.perfil || 'vistoriador'));
    tela.style.display = 'none'; aoEntrar(u, info || {});
  };
  const mostrar = modo => {
    $('#lg-login').style.display = modo === 'login' ? 'block' : 'none';
    $('#lg-setup').style.display = modo === 'setup' ? 'block' : 'none';
    tela.style.display = 'flex';
    setTimeout(() => $(modo === 'login' ? '#lg-mat' : '#st-nome').focus(), 50);
  };
  $('#lg-login').onsubmit = async ev => {
    ev.preventDefault(); $('#lg-erro').textContent = '';
    try { entrar((await post('/api/login', {matricula: $('#lg-mat').value, senha: $('#lg-senha').value})).usuario); }
    catch (e) { $('#lg-erro').textContent = e instanceof TypeError ? 'Sem conexão com o notebook.' : e.message; }
  };
  $('#lg-setup').onsubmit = async ev => {
    ev.preventDefault(); $('#st-erro').textContent = '';
    if ($('#st-senha').value !== $('#st-senha2').value) { $('#st-erro').textContent = 'As senhas não conferem.'; return; }
    try { entrar((await post('/api/setup', {nome: $('#st-nome').value, matricula: $('#st-mat').value, senha: $('#st-senha').value})).usuario); }
    catch (e) { $('#st-erro').textContent = e.message; }
  };
  api('/api/eu').then(j => entrar(j.usuario)).catch(async e => {
    if (e.sessao) {
      try { mostrar((await api('/api/login_info')).precisa_admin ? 'setup' : 'login'); } catch (_) { mostrar('login'); }
    } else {
      const salvo = usuarioSalvo();
      if (salvo) entrar(salvo, {offline: true});
      else { mostrar('login'); $('#lg-erro').textContent = 'Sem conexão com o notebook.'; }
    }
  });
}
async function sairDoSistema() {
  try { await post('/api/logout'); } catch (e) {}
  try { localStorage.removeItem('usuario'); } catch (e) {}
  location.reload();
}

/* Janela "Alterar minha senha" */
function abrirAlterarSenha() {
  let m = $('#mSenha');
  if (!m) {
    m = document.createElement('div'); m.className = 'modal'; m.id = 'mSenha';
    m.innerHTML = `<div class="jan peq"><h2><i class="fa-solid fa-key"></i> Alterar minha senha</h2>
      <label>Senha atual</label><input type="password" id="sn-atual" autocomplete="current-password">
      <label>Nova senha (mínimo 4 caracteres)</label><input type="password" id="sn-nova" autocomplete="new-password">
      <label>Repita a nova senha</label><input type="password" id="sn-nova2" autocomplete="new-password">
      <div class="erro" id="sn-erro"></div>
      <div class="acoes"><button class="btn verde" id="sn-ok"><i class="fa-solid fa-check"></i> Salvar</button>
      <button class="btn sec" id="sn-cancela">Cancelar</button></div></div>`;
    document.body.appendChild(m);
    $('#sn-cancela').onclick = () => m.classList.remove('aberto');
    $('#sn-ok').onclick = async () => {
      if ($('#sn-nova').value !== $('#sn-nova2').value) { $('#sn-erro').textContent = 'As senhas novas não conferem.'; return; }
      try { await post('/api/minha_senha', {atual: $('#sn-atual').value, nova: $('#sn-nova').value}); m.classList.remove('aberto'); alert('Senha alterada.'); }
      catch (e) { $('#sn-erro').textContent = e instanceof TypeError ? 'Sem conexão com o notebook.' : e.message; }
    };
  }
  ['#sn-atual', '#sn-nova', '#sn-nova2'].forEach(s => $(s).value = ''); $('#sn-erro').textContent = '';
  m.classList.add('aberto'); setTimeout(() => $('#sn-atual').focus(), 50);
}
function mapaLink(lat, lon) { return `https://www.google.com/maps?q=${lat},${lon}`; }
function fmtData(s) { if (!s) return ''; const m = s.match(/(\d+)-(\d+)-(\d+) (\d+:\d+)/); return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}` : s; }
