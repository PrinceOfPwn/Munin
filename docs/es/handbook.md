# Manual completo de Munin

[English](../en/handbook.md) · [Português BR](../pt-BR/handbook.md) · [简体中文](../zh-CN/handbook.md)

## Descripción general

Munin es un runtime durable y gobernado por operadores para operaciones
autónomas de seguridad. Integra el adaptador Discord (superficie operador), API
autenticada, MCP, GUI web en desarrollo, ejecución LangGraph, eventos
persistentes, checkpoints, aprobaciones humanas, composición viva de
capacidades, conocimiento Hugin y reconocimiento Valravn.

La configuración verificada de v1.0.0 es **adaptador Discord + GitHub Actions +
MiMo V2.5**. La GUI web es la interfaz objetivo a largo plazo, pero sigue en
reparación tras bugs de frontend detectados en sesiones en vivo; trata las
afirmaciones solo-GUI como no verificadas hasta que pase el ciclo de arreglo.
Otras combinaciones pueden funcionar, pero son experimentales hasta
documentarse.

## Modelo central

Munin separa conocimiento, autoridad, ejecución y evidencia. El conocimiento
puede sugerir una ruta; solo la política del servidor y el alcance del operador
autorizan la ejecución. Las herramientas actúan; los eventos y artefactos
preservan lo ocurrido.

## Interfaces

Discord es la superficie de operador estable de v1.0.0: presencia, comandos,
threads y aprobaciones. La GUI web sigue en desarrollo activo. `/api/*` expone
operaciones autenticadas, `/mcp/` publica capacidades vivas. Ninguna interfaz
crea autoridad propia.

## Runs, eventos y recuperación

Cada conversación mantiene un thread estable. Los runs usan leases renovables.
Los eventos son reproducibles y los checkpoints conservan el estado ejecutable.
Tras una caída, la recuperación segura retoma el mismo thread sin regenerar la
historia. Las aprobaciones pendientes permanecen pausadas.

## Modos operativos

- **Standard:** aprobación por acción.
- **YOLO:** menos aprobaciones rutinarias dentro de alcance confiable.
- **GOAL:** objetivo y TODO durables.
- **BEAST:** planificación profunda y delegación con presupuestos controlados.

Los modos no eliminan auditoría, aprobación crítica, redacción de secretos ni
política del servidor.

## Sistema de capacidades

El registro vivo puede incluir tools nativas, Valravn, skills revisadas,
especialistas acotados, herramientas del autonomy kernel y capacidades `gen__*`.
El descubrimiento en runtime es autoritativo. Un archivo o skill no se vuelve
ejecutable por existir.

## Hugin y Valravn

Hugin aporta conocimiento pasivo con fuentes. Valravn recolecta evidencia IOC,
CVE, activos, web histórica, routing, dark web y navegador. Ambos son inputs
externos no confiables hasta validarse contra el objetivo autorizado.

## Perfiles Soul

La Soul incluida es una caracterización específica para CTF y laboratorios. No
es el default recomendado. Producción y defensa deberían usar un perfil neutral
o propio mediante `soul_propose_edit` y revisión humana. Soul nunca concede
autorización, tools ni alcance.

## Checklist operativo

1. Confirmar autorización escrita y alcance.
2. Validar salud, autenticación y orígenes permitidos.
3. Probar un ciclo completo de tool calling estructurado.
4. Inspeccionar el registro vivo de capacidades.
5. Persistir la base activa y los checkpoints.
6. Definir aprobadores y autoridad de cancelación.
7. Revisar evidencia, herramienta y argumentos durante toda la operación.

## Despliegue

El camino verificado es adaptador Discord en GitHub Actions con MiMo V2.5. La
GUI web se une una vez que pase su ciclo de reparación. Los runners
efímeros requieren persistencia explícita mediante artifacts o almacenamiento
remoto. Producción necesita volúmenes durables, ingreso protegido, secretos
fuertes, orígenes estrictos y políticas de retención.

## Modelo de seguridad

El servidor controla identidad, política, aprobaciones y estado. Contenido web,
resultados de proveedores y tarjetas Hugin son datos no confiables. Las
capacidades generadas atraviesan validación y los mismos controles que una tool
nativa. Una tool call exitosa no demuestra autorización ni éxito de la misión.

## Licencia

Munin usa PolyForm Noncommercial 1.0.0. Investigación y estudio no comercial
están permitidos; productos, servicios, consultoría y aplicaciones comerciales
internas requieren una licencia separada.

**Знание переживает битву.** — El conocimiento sobrevive a la batalla.
