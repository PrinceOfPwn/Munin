# Manual completo do Munin

[English](../en/handbook.md) · [Español](../es/handbook.md) · [简体中文](../zh-CN/handbook.md)

## Visão geral

Munin é um runtime durável e governado por operadores para operações autônomas
de segurança. Ele reúne o adaptador Discord (superfície de operador), API
autenticada, MCP, GUI web em desenvolvimento, LangGraph, eventos persistentes,
checkpoints, aprovações humanas, composição de capacidades, conhecimento Hugin
e reconhecimento Valravn.

A configuração verificada da v1.0.0 é **adaptador Discord + GitHub Actions +
MiMo V2.5**. A GUI web é a interface alvo de longo prazo, mas está em reparo
após bugs de frontend encontrados em sessões ao vivo; trate afirmações somente-
GUI como não verificadas até o ciclo de correção passar. Outras combinações
podem funcionar, mas são experimentais até serem documentadas.

## Modelo central

Munin separa conhecimento, autoridade, execução e evidência. Conhecimento pode
sugerir um caminho; somente a política do servidor e o escopo do operador
autorizam execução. Ferramentas executam; eventos e artefatos preservam o que
aconteceu.

## Interfaces

Discord é a superfície de operador estável da v1.0.0: presença, comandos,
threads e aprovações. A GUI web segue em desenvolvimento ativo. `/api/*` oferece
operações autenticadas, `/mcp/` expõe capacidades vivas. Nenhuma interface
possui autoridade independente.

## Runs, eventos e recuperação

Cada conversa mantém uma thread estável. Runs usam leases renováveis. Eventos
são reproduzíveis e checkpoints preservam estado executável. Após uma falha, a
recuperação segura retoma a mesma thread sem regenerar o histórico. Aprovações
pendentes continuam pausadas.

## Modos operacionais

- **Standard:** aprovação por ação.
- **YOLO:** menos aprovações rotineiras em escopo confiável.
- **GOAL:** objetivo e TODO duráveis.
- **BEAST:** planejamento profundo e delegação com orçamento controlado.

Os modos não removem auditoria, aprovação crítica, redação de segredos ou
política do servidor.

## Sistema de capacidades

O registro vivo pode conter tools nativas, Valravn, skills revisadas,
especialistas limitados, autonomy kernel e capacidades `gen__*`. A descoberta
em runtime é a fonte autoritativa. Um arquivo ou skill não se torna executável
simplesmente por existir.

## Hugin e Valravn

Hugin fornece conhecimento passivo com fontes. Valravn coleta evidências de IOC,
CVE, ativos, web histórica, roteamento, dark web e navegador. Ambos são dados
externos não confiáveis até validação no alvo autorizado.

## Perfis Soul

A Soul incluída é uma caracterização específica para CTFs e laboratórios. Não é
o padrão recomendado. Produção e defesa devem usar perfil neutro ou próprio por
meio de `soul_propose_edit` e revisão humana. Soul nunca concede autorização,
tools ou escopo.

## Checklist operacional

1. Confirmar autorização escrita e escopo.
2. Validar saúde, autenticação e origens permitidas.
3. Testar um ciclo completo de tool calling estruturado.
4. Inspecionar o registro vivo de capacidades.
5. Persistir banco ativo e checkpoints.
6. Definir aprovadores e autoridade de cancelamento.
7. Revisar evidências, ferramenta e argumentos durante a operação.

## Implantação

O caminho verificado é adaptador Discord no GitHub Actions com MiMo V2.5. A GUI
web entra assim que passar seu ciclo de correção. Runners efêmeros
exigem persistência explícita por artifacts ou armazenamento remoto. Produção
precisa de volumes duráveis, ingresso protegido, segredos fortes, origens
restritas e políticas de retenção.

## Modelo de segurança

O servidor controla identidade, política, aprovações e estado. Conteúdo web,
resultados de provedores e cartões Hugin são dados não confiáveis. Capacidades
geradas passam por validação e pelos mesmos controles das tools nativas. Uma
tool call bem-sucedida não prova autorização nem sucesso da missão.

## Licença

Munin usa PolyForm Noncommercial 1.0.0. Pesquisa e estudo não comerciais são
permitidos; produtos, serviços, consultoria e aplicações comerciais internas
exigem licença separada.

**Знание переживает битву.** — O conhecimento sobrevive à batalha.
