# Levantamento da Frota Municipal — Prefeitura de Caratinga

Sistema para **automatizar a vistoria visual dos veículos e máquinas** da frota municipal da Prefeitura de Caratinga, onde trabalho. O objetivo é tornar o levantamento mais **rápido**, **padronizado** e **organizado**: cada vistoria segue o mesmo roteiro, os dados e as fotos vão direto do celular para o notebook, e os relatórios saem prontos.

## Por que ele existe

A vistoria visual da frota envolve muitos veículos, muitas fotos e muita conferência de cadastro. Com o sistema:

- **Ganho de tempo:** o vistoriador segue um roteiro de fotos no celular, sem precisar anotar em papel nem renomear fotos depois. As fotos já ficam salvas por placa, com data, hora e GPS.
- **Padronização:** todos os veículos são fotografados nos mesmos ângulos (frente, traseira, laterais, placa, chassi, painel, interior, pneus, avarias e motor) e respondem às mesmas perguntas.
- **Organização:** um único banco de dados reúne o cadastro do patrimônio, as vistorias, as fotos e os relatórios. Nada se perde entre pastas, planilhas e mensagens.
- **Acompanhamento em tempo real:** o painel mostra o que já foi feito, o que falta e as divergências (não localizados, veículos fora da lista, avarias, itens de segurança ausentes...).
- **Relatórios prontos e verificáveis:** o PDF de cada veículo é gerado ao concluir a vistoria, e os relatórios gerais têm número de controle e código de autenticidade.
- **Funciona no pátio:** o notebook faz o papel de servidor (sem internet) e o celular continua vistoriando mesmo fora do alcance do hotspot, enviando tudo quando a conexão volta.

## Principais funções

| Painel (notebook) | Aplicativo de vistoria (celular) |
|---|---|
| Importa a planilha do patrimônio (CSV ou Excel) | Lista de veículos com busca e filtros |
| Cartões, progresso geral e andamento por secretaria | Situação aparente, odômetro, local, motorista |
| Pendências, divergências e alertas por veículo | Itens de segurança (estepe, triângulo, macaco, chave) |
| Mapa das vistorias (GPS) | Roteiro de fotos com 11 etapas, revisão e conclusão |
| Relatórios PDF: por veículo, geral, por secretaria e pendências | Cadastro de veículo encontrado fora da lista |
| Verificação de autenticidade dos relatórios | GPS em cada foto |
| Usuários com perfis (administrador e vistoriador) | Modo offline com envio automático |
| Exportação em CSV | Instalável na tela inicial (PWA) |

## Como funciona

```
  Celular (vistoriador)  ──Wi-Fi do hotspot──▶  Notebook (servidor.py)
    fotos, GPS, dados                             banco SQLite, fotos, PDFs
                                                  painel em http://localhost:8000
```

O notebook roda um servidor local feito em Python e cria o próprio certificado de segurança, para que o celular possa usar o GPS e o modo offline. Não é preciso internet durante o uso (só o fundo do mapa depende dela).

## Requisitos

- Notebook com Windows e **Python 3** instalado (marque *Add Python to PATH* na instalação).
- Celular Android ou iPhone com navegador.
- Internet apenas na instalação.

## Instalação (uma única vez, com internet)

Dois cliques em **`instalar.bat`**. Ele instala as bibliotecas (PDF, QR Code, certificado) e cria o certificado local.

## Uso diário

1. Ligue o **Hotspot móvel** do Windows e conecte o celular a ele.
2. Dois cliques em **`iniciar.bat`**. O painel abre sozinho (`http://localhost:8000`).
3. **Primeiro uso:** crie o usuário **administrador** (nome, matrícula e senha).
4. Em **Usuários**, o administrador cria os vistoriadores (matrícula + senha inicial). Cada um pode trocar a própria senha em **Alterar senha**.

### Celular novo (uma vez por aparelho)

Isso tira o aviso de "conexão não segura" e libera o GPS e o modo offline.

1. No painel, toque em **Celular novo? Instalar certificado** e leia o QR Code (ou abra `http://IP-DO-NOTEBOOK:8000/certificado` no celular).
2. Baixe e instale o certificado (o passo a passo para Android e iPhone está na própria página).
3. Leia o QR Code principal do painel e entre **com conexão** uma vez, para o celular baixar a lista de veículos. Opcional: *Adicionar à tela inicial*.
4. Se o IP do notebook mudar, **não** é preciso instalar nada de novo.

### Modo offline

- Longe do hotspot, a vistoria continua normalmente: fotos, dados e GPS ficam salvos no celular. No topo aparece *Sem conexão · N envios pendentes*.
- Ao voltar ao alcance do notebook, tudo é enviado sozinho e o relatório PDF é gerado.
- **Não limpe os dados do navegador** enquanto houver envios pendentes.
- Os horários registrados são os do momento da foto ou da vistoria.

### Perfis

- **Administrador:** tudo (importar, editar, excluir, zerar banco, usuários).
- **Vistoriador:** vistoria no celular, consulta no painel e geração de relatórios.
- 5 senhas erradas seguidas bloqueiam a matrícula por 5 minutos.

### Relatórios

- Botão **Relatórios** no painel: geral da frota, por secretaria e pendências (todas ou por secretaria, com campo de ciência do chefe do setor).
- O relatório de cada veículo é gerado ao concluir a vistoria.
- Todo PDF tem **número de controle**, **código de autenticidade** e QR Code no rodapé. A verificação é feita no painel (*Relatórios › Verificar*) ou pelo QR Code, na rede do notebook.

## Onde ficam os arquivos (pasta `dados`)

| Pasta | Conteúdo |
|---|---|
| `dados\fotos\PLACA\` | fotos de cada veículo |
| `dados\relatorios\` | relatórios em PDF |
| `dados\frota.db` | banco de dados (inclui usuários e números de controle) |
| `dados\certificado\` | certificados (**não** compartilhe `ca-chave.pem` nem `chave.pem`) |
| `dados\backups\` | cópias feitas pelo botão *Zerar banco* |
| `dados\excluidos\` | fotos de veículos excluídos |

> **Faça backup copiando a pasta `dados` todos os dias.**

## Personalizar o cabeçalho do PDF

Edite a lista `CABECALHO` no início de `relatorio.py`. O brasão é `web\brasao.png`.

## Atualizando de uma versão antiga

Extraia o ZIP novo sobre a pasta antiga e **não apague a pasta `dados`**. O banco é atualizado sozinho. Ao abrir, o sistema pede para criar o administrador.

## Estrutura do projeto

```
frota_caratinga/
├── servidor.py          servidor local: painel, API, HTTPS, banco de dados
├── relatorio.py         geração dos relatórios em PDF
├── instalar.bat         instalação única
├── iniciar.bat          abre o sistema
├── README.md
├── web/
│   ├── painel.html      painel do notebook
│   ├── celular.html     aplicativo de vistoria
│   ├── certificado.html instruções do certificado no celular
│   ├── comum.js         funções comuns (login, comunicação com o servidor)
│   ├── estilo.css       aparência
│   ├── sw.js            service worker (modo offline)
│   └── manifest.webmanifest, brasao.png, icon-192.png, icon-512.png
└── dados/               criada no primeiro uso (banco, fotos, relatórios, certificados)
```

## Tecnologias

- **Python** (biblioteca padrão: `http.server`, `sqlite3`), **reportlab** (PDF), **Pillow** (miniaturas), **segno** (QR Code), **cryptography** (certificado local), **openpyxl** (Excel).
- **HTML, CSS e JavaScript** puros, sem frameworks. IndexedDB e service worker para o modo offline.
- Mapa com fundo do **OpenStreetMap**.
