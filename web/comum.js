/* Funções comuns ao painel (PC) e à tela do celular */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const STATUS = {pendente: 'Pendente', em_andamento: 'Em andamento', concluido: 'Concluído'};
const badge = s => `<span class="badge b-${s}">${STATUS[s] || s}</span>`;

async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  const t = await r.text();
  let j; try { j = JSON.parse(t); } catch (e) { throw new Error('Resposta inválida do servidor'); }
  if (j.ok === false) throw new Error(j.erro || 'Erro');
  return j;
}
const post = (url, dados) => api(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados || {})});

function mapaLink(lat, lon) { return `https://www.google.com/maps?q=${lat},${lon}`; }
function fmtData(s) { if (!s) return ''; const m = s.match(/(\d+)-(\d+)-(\d+) (\d+:\d+)/); return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}` : s; }
