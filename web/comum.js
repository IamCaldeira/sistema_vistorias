/* Funções auxiliares do painel */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  const t = await r.text();
  let j; try { j = JSON.parse(t); } catch (e) { throw new Error('Resposta inválida do servidor'); }
  if (j.ok === false) throw new Error(j.erro || 'Erro');
  return j;
}
const post = (url, dados) => api(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados || {})});
