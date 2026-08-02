---
name: hugin-navigate-hugin-graph
description: "Meta-skill: teaches an agent how to navigate the Hugin knowledge graph (https://github.com/PrinceOfPwn/Hugin) to extract low-level Rust + Red Team tradecraft. Covers graph schema, node types, edge types, technique/playbook/chain structure, Rust module map, and concrete extraction recipes (Python snippets). Use when you need to mine Hugin for original research, generate new technique cards, or cross-reference MITRE ATT&CK mappings with Rust implementation details."
---

# Guía de navegación del grafo Hugin para extraer conocimiento Rust + Red Team

> **Objetivo:** enseñar a un agente (humano o LLM) a navegar el repositorio
> **<https://github.com/PrinceOfPwn/Hugin>** y su grafo de conocimiento, con
> foco en **bajo nivel de Rust aplicado a red team** (syscalls indirectos,
> inyección de procesos, evasión de EDR, persistencia, ofuscación de sleep,
> C2 y exfiltración).
>
> Esta guía **no** enseña a operar — enseña a **leer y exprimir** Hugin
> como fuente de conocimiento reutilizable.

---

## 1. Qué es Hugin realmente

Hugin **no es un framework de explotación**. Es un **observatorio estático de
conocimiento ofensivo** construido con Astro + React + WebGL sobre GitHub Pages.
Su grafo `data/source/public-graph.json` es la **única fuente de verdad** y
contiene:

| Tipo de nodo        | Cantidad | Para qué sirve                                          |
|---------------------|---------:|----------------------------------------------------------|
| `technique` (T-NNN) |        58 | Tarjetas de técnica ofensiva con code-snippets Rust/C/ASM |
| `playbook`          |        36 | Implementación real: archivos, funciones, líneas, dependencias entre técnicas |
| `chain`             |       213 | Cadenas de técnicas encadenadas paso-a-paso (ej. *Process Hollowing Chain*) |
| `documentation`     |       236 | Documentación de módulos de código fuente (incluye 35 módulos Rust) |
| `detection`         |       524 | Superficies de detección (ETW, Sysmon, callbacks, hooks EDR) — útil para evasión |
| `concept`           |       717 | Conceptos atómicos del bajo nivel Win32/NT |
| `evidence`          |     3 256 | Extractos anónimos de evidencia (código, logs, dumps) |
| `lgtm_note`         |       344 | Notas internas del cluster LGTM (flujo de razonamiento del analista) |
| `attack-pattern`    |       224 | Patrones de ataque catalogados |
| `coverage-gap`      |       149 | Huecos de cobertura detectados — **oportunidades de investigación** |
| `proposed-technique`|       106 | Técnicas candidatas generadas por GLM-5.2 |
| `cross-source-convergence` | 63 | Convergencias entre fuentes independientes |
| `emerging-tradecraft` |      26 | Tradecraft emergente |

**Total:** 5 605 nodos · 3 919 relaciones tipadas · 5 605 documentos Markdown
embebidos en `contents`.

### 1.1 Origen del conocimiento

El grafo proviene de un **vault privado** importado por el operador
(`hugin/vault-export/graph.json`, ignorado por Git). El importador:

1. **Anonimiza** nombres de providers, rutas privadas, usernames, filenames y links promocionales.
2. **Remapea** IDs de evidencia a IDs neutrales (`EV-XXXXXXXX`).
3. **Cuarantena** relaciones inválidas y fragmentos de bajo valor.
4. **Publica** solo lo que pasa los filtros → `data/source/public-graph.json`.

El repositorio upstream **Munin** (no público) es el *harvester* que recolecta
artefactos crudos. Hugin es la proyección saneada.

### 1.2 El pipeline vivo (GitHub Actions)

Hugin no es estático: se actualiza automáticamente cada 4 horas vía
`expand-cards.yml` que ejecuta **GLM-5.2** (vía NVIDIA Integrate) para generar
**nuevas tarjetas T-NNN** desde clusters LGTM pendientes.

| Workflow            | Trigger                            | LLM usado                          | Output |
|---------------------|------------------------------------|------------------------------------|--------|
| `expand-cards.yml`  | cron 4h @ :15 / manual             | GLM-5.2                            | Nuevos `T-NNN-*.md` en `data/incoming/` |
| `ingest-v2.yml`     | push `data/incoming/**` / dispatch | Qwen 3.5-4B ONNX q4 + GLM-5.2      | `public-graph.json` actualizado |
| `pages.yml`         | push non-jsonl / dispatch          | —                                  | Deploy a GitHub Pages |
| `quality.yml`       | pull_request / manual              | —                                  | Playwright + Lighthouse CI |
| `release.yml`       | manual (input `version`)           | —                                  | GitHub Release taggeado |

**Implicación:** cada ~4h puede haber nuevas tarjetas. Si clonas el repo hoy,
dentro de una semana habrá más técnicas. **Sincroniza con `git pull`** antes de
cualquier análisis serio.

---

## 2. Mapa físico del repositorio

```
Hugin/
├── data/
│   ├── incoming/              # Crudo: .rs, .md, .c, .py, .jsonl soltos
│   │   └── bundle-20260728/   # Tarjetas T-NNN en Markdown
│   ├── normalized/            # JSONL con esquema canonical
│   ├── reference/
│   │   └── mitre-enterprise.json   # ATT&CK v19.1 completo
│   └── source/
│       ├── public-graph.json  # ★ LA FUENTE DE VERDAD (18 MB)
│       ├── ingest-manifest.json
│       └── README.md
├── docs/
│   └── PIPELINE.md            # ★ Cómo funciona todo el pipeline
├── prompts/
│   └── glm-expand-cluster-to-card.md   # ★ Prompt que genera nuevas T-NNN
├── scripts/                   # Pipelines JS (.mjs) — útiles para re-procesar
├── src/
│   ├── components/            # React islands (grafo 3D, ⌘K palette, filtros)
│   ├── pages/                 # Rutas Astro: /techniques, /chains, /explore, /mitre, ...
│   └── lib/                   # Tipos + utilidades
└── tests/                     # E2E Playwright + contratos de ingestión
```

### 2.1 Interfaces web públicas (sin clonar)

| URL | Para qué |
|-----|----------|
| <https://princeofpwn.github.io/Hugin/> | Dashboard general |
| <https://princeofpwn.github.io/Hugin/explore/> | **Catalog 3-columnas** (master/detail/preview). Mejor para scrollear técnicas. |
| <https://princeofpwn.github.io/Hugin/graph/> | Universo 3D WebGL — busca nodos por etiqueta, navega vecinos |
| <https://princeofpwn.github.io/Hugin/latest/> | Últimas 50 entradas |
| <https://princeofpwn.github.io/Hugin/mitre/> | Matriz ATT&CK con cobertura por táctica |
| <https://princeofpwn.github.io/Hugin/dataset/> | Contrato del dataset (schemas) |
| <https://princeofpwn.github.io/Hugin/quality/> | Telemetría de calidad del grafo |
| <https://princeofpwn.github.io/Hugin/techniques/T-001/> | Página permanente de cada técnica |
| <https://princeofpwn.github.io/Hugin/chains/> | Índice de attack chains |
| <https://princeofpwn.github.io/Hugin/tradecraft/> | Tradecraft Q&A |

---

## 3. El grafo `public-graph.json` por dentro

### 3.1 Top-level shape

```jsonc
{
  "schemaVersion": "...",
  "ownerAuthorization": "...",
  "sourceHash": "...",
  "rawCounts": { "nodes": 5608, "relations": 3919 },
  "quality": {
    "states": [...],
    "quarantinedNodes": [...],
    "quarantinedRelations": [...],
    "rules": [...]
  },
  "nodes":    [ /* 5605 objetos */ ],
  "edges":    [ /* 3919 relaciones */ ],
  "contents": { "T-001": "# RecycledGate Indirect Syscalls\n...", /* ... */ }
}
```

`contents` es un dict `node_id -> markdown` con el contenido completo de cada
nodo. **Es donde vive el conocimiento real** — los metadatos en `nodes` solo
sirven para indexar y relacionar.

### 3.2 Shape de un nodo `technique`

```jsonc
{
  "id":           "T-024",
  "label":        "Host Survey and Situational Awareness: Unified Reconnaissance",
  "category":     "discovery",           // syscalls, process-injection, edr-evasion, ...
  "tier":         "A",                    // S = bleeding edge, A = production, B = foundational
  "type":         "technique",
  "color":        "#ffffff",
  "size":         45,                     // tamaño visual en el grafo
  "mitre":        "T1082",
  "tags":         ["host-survey", "discovery", "ntquerysysteminformation", ...],
  "origin":       "atlas-synthesis",
  "file":         "techniques/T-024-host-survey-situational-awareness.md",
  "publishState": "core",                 // core | support | evidence
  "evidenceId":   "EV-A21BDEC104",
  "sourceClass":  "owner-authorized-research",
  "sourceHash":   "d14fab39522e69f8"
}
```

### 3.3 Tipos de relación (edges)

| type              | count | Significado |
|-------------------|------:|-------------|
| `concept_link`    | 1813  | Enlace temático entre conceptos |
| `reference`       |  940  | Una técnica/documento referencia a otro |
| `chains_to`       |  296  | Una técnica encadena con otra en un chain |
| `enables`         |  216  | T-X habilita T-Y (T-X es prerrequisito funcional) |
| `implements`      |  177  | Una playbook implementa una técnica |
| `requires`        |  123  | T-X requiere T-Y (hard prerequisite) |
| `alternative_to`  |   84  | T-X es alternativa a T-Y |
| `derived_from`    |   80  | T-X deriva de T-Y |
| `related`         |   59  | Relación genérica |
| `mentions`        |   34  | Mención textual |
| `enhances`        |   32  | T-X mejora T-Y |
| `detects`         |   22  | Una detection detecta una técnica |
| `uses`            |   19  | Una técnica usa un concepto/estructura |
| `counters`        |   18  | Una detection mitiga una técnica |
| `related_to`      |   3   | Alias |
| `depends_on`      |   3   | Hard dependency |

**Uso típico:** para ver qué técnicas *habilita* `T-001 RecycledGate`, filtra
`edges` por `{source:"T-001", type:"enables"}`.

### 3.4 Las 13 categorías de técnicas

| Categoría          | Count | Técnicas destacadas |
|--------------------|------:|---------------------|
| `syscalls`         | 6     | T-001 RecycledGate, T-002 Hell's Gate, T-003 VEH Gate, T-004 PEB Walker, T-006 Phantom Stubs, T-049 Heaven's Gate, T-050 GetProcAddress |
| `process-injection`| 9     | T-007 Pool Party, T-008 Threadless, T-009 Ghosting, T-010 Herpaderping, T-011 Dirty Vanity, T-012 Early Cascade APC, T-013 loaders, T-014 NtCreateUserProcess, T-015 PPID Spoofing, T-046 sRDI, T-047 Cross-session, T-048 Custom loader |
| `edr-evasion`      | 3     | T-016 Suite (12 sub-técnicas), T-030 Inline Hook, T-031 WldpQueryDynamicCodeTrust |
| `persistence`      | 9     | T-017 Five-Layer, T-018 Edo Tensei, T-034 IFEO, T-035 Port Monitor, T-036 SCM Service, T-037 WMI Subscription, T-038 AppInit_DLLs, T-039 Binary Patching, T-040 SERVICE_FAILURE_ACTIONS, T-041 Service Hiding |
| `sleep-obfuscation`| 1     | T-005 Ekko ROP Sleep |
| `anti-analysis`    | 1     | T-020 Suite |
| `crypto`           | 1     | T-021 Cryptography & Obfuscation |
| `networking`       | 4     | T-019 Edo Dead Drop C2, T-022 Network Suite, T-032 C2 Beaconing, T-033 Named Pipes IPC |
| `client`           | 1     | T-023 Client Capabilities Suite |
| `discovery`        | 6     | T-024 Host Survey, T-025 LotL Recon, T-026 DPAPI, T-027 KUSER_SHARED_DATA, T-028 Patch enum, T-029 SDDL recon |
| `privesc`          | 4     | T-042 SeBackup/SeRestore, T-043 Token Theft, T-044 SCM privesc, T-045 SeDebugPrivilege |
| `asm`              | —     | (15 nodos concept) |
| `cpp`              | —     | (12 nodos concept) |

**Categorías con `tier: S` (bleeding edge):** T-001, T-002, T-003, T-005,
T-007, T-009, T-012, T-014, T-015, T-017, T-018, T-019, T-042.

---

## 4. Dónde está el código Rust

Hugin documenta un **implant ofensivo en Rust** con módulos nombrados como
jutsus de Naruto. El árbol de módulos documentados (35 nodos) es:

```
src/client/rust/
├── build.rs
├── src/
│   ├── main.rs                # Entry point
│   ├── amaterasu.rs           # Exfiltration engine (1107 líneas)
│   ├── byakugan.rs            # (ver doc node)
│   ├── juubi.rs               # Componente
│   ├── juubi_chain.rs         # Chain del componente juubi
│   ├── kamui.rs               # (ver doc node)
│   ├── henge.rs               # Transformación/ducking
│   ├── kotoamatsukami.rs      # (ver doc node)
│   ├── browser.rs             # Browser hooking/stealing
│   ├── browser_hook.rs
│   ├── browser_session.rs
│   ├── capture.rs             # Screen capture
│   ├── clipboard.rs           # Clipboard logger
│   ├── commands.rs            # Command dispatcher
│   ├── config.rs              # Config parser
│   ├── cursor_hider.rs        # Anti-forensic UI
│   ├── dirty_rect.rs          # h264 delta encoding para VNC
│   ├── discovery.rs           # Recon host survey
│   ├── eth_rpc.rs             # Ethereum RPC client (Web3 wallet theft?)
│   ├── eth_tx.rs              # ETH transaction signing
│   ├── h264_encoder.rs        # Video encoding
│   ├── html_overlay.rs        # hVNC overlay rendering
│   ├── http_poll_transport.rs # HTTP polling C2 transport
│   ├── hvnc.rs                # Hidden VNC (control remoto sin UI visible)
│   ├── input.rs               # Input injection
│   ├── input_blocker.rs       # Block user input during operation
│   ├── keylogger.rs           # Keylogger
│   ├── overlay.rs             # Overlay rendering base
│   ├── protocol.rs            # Wire protocol (message types)
│   ├── self_delete.rs         # Self-destruct
│   ├── sysinfo_collect.rs     # System inventory
│   ├── tcp_transport.rs       # Raw TCP C2 transport
│   ├── ui_automation.rs       # UI Automation API hooking
│   └── vnc_server.rs          # VNC server impl
```

### 4.1 Cómo se referencia el Rust en las playbooks

Las playbooks **no** mencionan `client_rust` directamente (está anonimizado).
En su lugar, usan el identificador `dark_crystal/` como alias del vault
interno:

```yaml
# playbook T-001
vault_references:
  - dark_crystal/crowd/src/recycled.rs
  - dark_crystal/crates/core/src/sys_recycled.rs
  - dark_crystal/crates/core/src/sys_indirect.rs
implements:
  - file: dark_crystal/crowd/src/recycled.rs
    key_functions: [recycled1, recycled2, ..., nt_create_user_process, ...]
    lines_of_interest:
      - "L20-L38: recycled1 stub — sub rsp 0x28 / call r11 / add rsp 0x28"
      - "L260-L285: invoke() dispatcher — hash→(ssn,gadget) lookup"
```

### 4.2 Convención de directorios del vault

| Path anonimizado                  | Equivalente real (deducible)       |
|-----------------------------------|-------------------------------------|
| `dark_crystal/crowd/src/*.rs`     | Crates de syscall stubs y primitives ofensivas |
| `dark_crystal/crates/core/*.rs`   | Core library reutilizable (sys_recycled, ekko_variants, sys_indirect, stack_spoof) |
| `src/client_rust/src/*.rs`        | Implant final con módulos Naruto |

Es decir, el código vive en **tres capas**:

1. **`crowd/`** — implementaciones crudas de syscalls (Hell's Gate, RecycledGate, Halo's Gate).
2. **`crates/core/`** — wrappers Rust idiomáticos sobre `crowd/` (sys_recycled.rs, ekko_variants.rs, sys_indirect.rs).
3. **`src/client_rust/src/`** — el implant completo que consume `crates/core`.

---

## 5. Técnicas Rust + Red Team por categoría (índice navegable)

> Para cada técnica: ID · tier · MITRE · archivo Rust referenciado · líneas clave.

### 5.1 Syscalls indirectos (categoría `syscalls`)

| ID | Tier | MITRE | Técnica | Archivo Rust | Líneas clave |
|----|------|-------|---------|--------------|--------------|
| T-001 | S | T1106 | **RecycledGate Indirect Syscalls** | `crowd/src/recycled.rs`, `crates/core/src/sys_recycled.rs`, `crates/core/src/sys_indirect.rs` | L20-L38 recycled1 stub; L260-L285 invoke() dispatcher |
| T-002 | S | T1106 | **Hell's Gate / Halo's Gate / Tartarus Gate + FreshyCalls** | `crowd/src/hells_gate.rs::read_ssn_from_stub` (L202-L226), `crowd/src/hells_gate.rs::find_ntdll_base` (L85-L122) | Sort by RVA → SSN; fallback scan 0F 05 C3 |
| T-003 | S | — | **VEH Syscall Gate** | (ver playbook) | VEH handler como trampoline |
| T-004 | A | — | **PEB Walker via gs:[0x60]** | inline asm `mov {}, gs:[0x60]` | InMemoryOrderModuleList walk |
| T-006 | A | — | **Phantom Stubs (MEM_IMAGE Syscall Stubs)** | `crates/core/src/sys_recycled.rs` | `mov r10, rcx; jmp r11` con `options(nostack)` |
| T-049 | A | — | **Heaven's Gate (32→64-bit syscall transition)** | (ver playbook) | WOW64 transition |
| T-050 | A | — | **Manual GetProcAddress via Export Table Walking** | (ver playbook) | IMAGE_EXPORT_DIRECTORY walk |

**Snippets clave** (extraídos del contenido del grafo):

```rust
// crates/core/src/sys_recycled.rs — variante mínima
unsafe asm!(
    "mov r10, rcx",
    "jmp r11",
    in("r11") gadget_addr,
    options(nostack),
);
// No shadow space: jmp no push return address; ntdll ret pops caller's
// return directly. Rust ABI guarantees caller-provided 0x20 home space.
```

```rust
// crowd/src/hells_gate.rs — SSN extraction by RVA sort
for (i, &rva) in zw_funcs.iter().enumerate() {
    if rva == target_func_rva { ssn = i as u32; break; }
}
// Then scan forward up to 512 bytes from target_ptr for 0F 05 C3 (syscall;ret)
```

### 5.2 Inyección de procesos (`process-injection`)

| ID | Tier | MITRE | Técnica | Arquitectura Rust |
|----|------|-------|---------|-------------------|
| T-007 | S | T1055 | **Pool Party Injection** | `nt_create_section` + `nt_map_view_of_section` + `nt_set_information_worker_factory` (ThreadPool aliasing) |
| T-008 | A | T1055 | **Threadless Injection (Export Hijacking)** | Sobreescritura de export table entry sin crear hilo |
| T-009 | S | T1055 | **Process Ghosting** | `nt_create_process_ex` con section backed por archivo ya borrado |
| T-010 | A | T1055 | **Process Herpaderping** | `nt_create_user_process` + `nt_write_file`/`nt_set_information_file`/`nt_flush_buffers_file` triad |
| T-011 | A | T1055 | **Dirty Vanity (Process Reflection)** | `RtlCreateProcessReflection` |
| T-012 | S | T1055 | **Early Cascade APC Injection** | `NtCreateThreadEx` suspendida + `NtQueueApcThread` antes de resume |
| T-014 | S | T1055 | **NtCreateUserProcess (Direct NT Process Creation)** | PPID spoofing L488-L525 |
| T-015 | S | T1055 | **PPID Spoofing** | `PROCESS_INFORMATION` con `ParentProcess` atributo |
| T-046 | A | T1055 | **Manual PE Loader + Reflective DLL Injection (sRDI)** | Parser PE manual + shellcode conversion |
| T-047 | A | T1055 | **Cross-Session Process Injection via WTS** | `WTSEnumerateProcessesEx` + cross-session `OpenProcess` |

### 5.3 Evasión de EDR (`edr-evasion`)

| ID | Tier | MITRE | Técnica | Detalle |
|----|------|-------|---------|---------|
| T-016 | mixed | T1562 | **EDR Evasion Suite (12 técnicas)** | (1) BYOVD kernel driver kill, (2) NTDLL unhook, (3) AMSI patch, (4) ETW patch, (5) stack spoof, (6) PEB unlink, (7) module stomping, (8) callback repointing, (9) thread hide, (10) DKOM, (11) hardware breakpoint hooking, (12) image relocation spoofing |
| T-030 | B | T1055 | **Inline Hook Implementation** | Red-team hooking mechanics (5-byte jmp patching) |
| T-031 | A | T1218 | **WldpQueryDynamicCodeTrust** | WLDP API check antes de ejecutar shellcode dinámico |

### 5.4 Sleep obfuscation

| ID | Tier | MITRE | Técnica | Detalle Rust |
|----|------|-------|---------|--------------|
| T-005 | S | T1497.003 | **Ekko ROP Sleep Obfuscation** | `crowd/src/sleep.rs::ekko()` + `crates/core/src/ekko_variants.rs` · 6-frame ROP chain en timer-queue thread · VirtualProtect PAGE_READWRITE + RC4 in-place encrypt durante sleep · `NtContinue` con CONTEXT frames crafted · anti-sandbox jitter divisor=8 |

### 5.5 Persistencia (`persistence`)

| ID | Tier | MITRE | Mecanismo |
|----|------|-------|-----------|
| T-017 | S | T1547 | **Five-Layer Persistence con Resilience Monitor** | COM hijack + NTFS EA + schtask + TLS callback + phantom_restart · `resilience_loop()` cada 30 min reintala lo que falte |
| T-018 | S | T1546 | **Edo Tensei (Polymorphic Resurrection Engine)** | Polimorfismo per-gen (T-001/T-002/T-003/T-005/T-017 mutan) · `read_gen_registry_inner()` + `write_gen_registry_inner()` persisten el generation counter en `HKCU\...` |
| T-034 | A | T1546 | **IFEO GlobalFlag + SilentProcessExit** | `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\<exe>\GlobalFlag` + `SilentProcessExit`Reporting`| 
| T-035 | A | T1547 | **Port Monitor Persistence (Print Spooler)** | `AddMonitorEx` con DLL maliciosa |
| T-036 | A | T1543 | **Windows Service-Based Persistence via SCM** | `OpenSCManager` + `CreateService` con `SERVICE_AUTO_START` |
| T-037 | A | T1546 | **WMI Permanent Event Subscription** | `__EventFilter` + `CommandLineEventConsumer` + `__FilterToConsumerBinding` |
| T-038 | A | T1106 | **AppInit_DLLs Registry Persistence** | `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows\AppInit_DLLs` |
| T-039 | B | — | **On-Disk Binary Patching** | Patchear binario legítimo en disco |
| T-040 | B | T1543 | **SERVICE_FAILURE_ACTIONS Crash-Triggered** | `ChangeServiceConfig` con `SERVICE_FAILURE_ACTIONS` flag que ejecuta comando al crashear |
| T-041 | B | T1564 | **Service Hiding from SCM** | `ChangeServiceConfig` con `SERVICE_CONFIG_SERVICE_PRESHUTDOWN_INFO` o patch de `services.exe` |

### 5.6 C2 y networking (`networking`)

| ID | Tier | MITRE | Técnica |
|----|------|-------|---------|
| T-019 | S | T1071 | **Edo Dead Drop (Autonomous C2 Channels)** | Steganografía LSB en BMP descargado por HTTP · `extract_lsb_from_bmp()` parser BMP puro-Rust sin GDI+ · AES key hardcoded `EDO_DROP_AES_KEY` |
| T-022 | mixed | — | **Network and Protocol Suite** | Múltiples protocolos C2 |
| T-032 | B | T1071 | **C2 Check-in and Beaconing** | Pattern de jitter beacon |
| T-033 | A | T1559 | **Named Pipes for C2 + IPC** | `\\.\pipe\<name>` impersonation |

### 5.7 Anti-analysis, crypto, client suites

| ID | Tier | Técnica |
|----|------|---------|
| T-020 | mixed | **Anti-Analysis Suite** — sandbox detection, sleep-skipping, debugger traps |
| T-021 | mixed | **Cryptography and Obfuscation** — AES, RC4, XOR, string obfuscation |
| T-023 | mixed | **Client Capabilities Suite** — exfil, keylog, clipboard, hVNC, browser hook |

### 5.8 Discovery & privesc

| ID | Tier | MITRE | Técnica |
|----|------|-------|---------|
| T-024 | A | T1082 | **Host Survey & Situational Awareness** — unified recon primitive |
| T-025 | A | T1057 | **Custom Recon Tooling via LotL API Reimplementation** |
| T-026 | A | T1003 | **DPAPI Master Key Extraction** |
| T-027 | A | T1082 | **KUSER_SHARED_DATA Direct-Read** — bypass API calls |
| T-028 | B | T1070 | **Patch and Hotfix Enumeration** |
| T-029 | B | T1007 | **SDDL & Security Descriptor Recon** |
| T-042 | S | T1005 | **SeBackupPrivilege / SeRestorePrivilege ACL Bypass** |
| T-043 | A | T1134 | **Token Theft (TokenThief)** — `OpenProcessToken` + `DuplicateTokenEx` |
| T-044 | A | T1574 | **Service-Based LPE via SCM Enumeration** |
| T-045 | A | T1134 | **SeDebugPrivilege Abuse** |

---

## 6. Attack Chains (213 cadenas) — las más relevantes para Rust

Las **attack chains** son secuencias ordenadas de técnicas con justificación
operacional. Las más útiles para entender el flujo Rust son:

### 6.1 Inyección vía syscalls indirectos
```
chain:process-injection-target-selection-via-native-enum
  → T-024 (Host Survey)
  → T-025 (Custom Recon LotL)
  → T-007 (Pool Party) | T-009 (Process Ghosting) | T-012 (Early Cascade APC)
  → T-001 (RecycledGate) — syscall backend
  → T-005 (Ekko Sleep) — entre polls
```

### 6.2 Unhook + syscall directo
```
chain:fresh-copy-ntdll-unhook
  1. CreateFileA(ntdll.dll, READ)
  2. CreateFileMapping(hNtdll, READ_ONLY | SEC_IMAGE)
  3. MapViewOfFile + locate NtHeader + .text bounds
  4. T-016 — memcpy fresh .text bytes over hooked .text region
  → ahora cualquier Nt* call va al stub limpio (aunque RecycledGate ya no lo necesite)
```

### 6.3 Five-layer persistence + resurrection
```
T-017 (Five-Layer Persist)  ─┐
T-018 (Edo Tensei polymorph) ─┤── resilience_loop cada 30 min reintala
T-019 (Edo Dead Drop C2)    ─┘   lo que falte; T-005 protege sleep
```

### 6.4 Otras cadenas clave

| Chain | Descripción |
|-------|-------------|
| `thread-hijacking-injection-chain` | Suspend → GetThreadContext → patch RIP → SetThreadContext → ResumeThread |
| `process-hollowing-chain` | CreateProcess suspended → hollow → write → resume |
| `pe-injection-chain-(non-hollowing)` | PE-stomp sin hollowing |
| `windows-service-persistence-chain` | SCM service install + failure actions |
| `apc-injection-chain-(queueuserapc)` | QueueUserAPC en hilo alertable |
| `classic-dll-injection-via-loadlibrarya` | LoadLibraryA en proceso remoto |
| `reflective-dll-injection-(rdi)` | sRDI conversion a shellcode |
| `iat-hooking-workflow` | IAT patching |
| `runtime-ssn-resolution-cascade` | Hell's Gate → Halo's Gate → Tartarus → FreshyCalls fallback cascade |
| `com-hijack-persistence-installation` | HKCU\Software\Classes\CLSID overwrite |
| `uac-bypass-via-autoelevate-binary-weaponization` | Autoelevate binary abuso |
| `device-guard-trust-probe-before-shellcode-execution` | T-031 WldpQueryDynamicCodeTrust antes de cargar shellcode |

---

## 7. Cómo extraer conocimiento de Hugin — recetas operativas

### 7.1 Receta A — "Quiero entender cómo se implementa T-XXX en Rust"

```python
# 1. Cargar grafo
import json
g = json.load(open("Hugin/data/source/public-graph.json"))
nodes, contents = g["nodes"], g["contents"]

# 2. Encontrar la técnica
tech = next(n for n in nodes if n["id"] == "T-001")
print(tech["label"], tech["mitre"], tech["tags"])

# 3. Imprimir el content (la tarjeta completa en Markdown)
print(contents["T-001"])

# 4. Buscar playbooks que la implementen
playbooks = [n for n in nodes if n.get("type") == "playbook"
             and f"T{tech['id'].split('-')[1].zfill(3)}" in n["id"]]
# o buscar por edges type="implements"
impl_edges = [e for e in g["edges"] if e.get("type")=="implements"
              and e.get("target") == tech["id"]]
for e in impl_edges:
    print(contents[e["source"]])  # playbook markdown

# 5. Buscar chains que la usen
chain_edges = [e for e in g["edges"] if e.get("type")=="chains_to"
               and (e.get("source")==tech["id"] or e.get("target")==tech["id"])]

# 6. Buscar documentación de módulos Rust relacionados
# (los doc:src__client_rust__* nodes mencionarán la técnica por ID)
for n in nodes:
    if n.get("id","").startswith("doc:src__client_rust"):
        body = contents.get(n["id"], "")
        if tech["id"] in body:
            print(n["id"], "→", n.get("label"))
```

### 7.2 Receta B — "Quiero ver todo el código Rust referenciado por las técnicas"

```python
import re
all_md = "\n".join(contents.values())
# Buscar bloques ```rust
rust_blocks = re.findall(r"```rust\n(.*?)```", all_md, re.DOTALL)
print(f"Total Rust code blocks: {len(rust_blocks)}")
# Filtrar los que tienen `unsafe` o `asm!` o `syscall`
unsafe_blocks = [b for b in rust_blocks if "unsafe" in b or "asm!" in b]
print(f"With unsafe/asm!: {len(unsafe_blocks)}")
```

### 7.3 Receta C — "Quiero mapear MITRE → técnicas Hugin → Rust"

```python
# MITRE ATT&CK Enterprise v19.1 ya está incluido
mitre = json.load(open("Hugin/data/reference/mitre-enterprise.json"))
# Indexar técnicas Hugin por MITRE ID
hugin_by_mitre = {}
for n in nodes:
    if n.get("type") == "technique" and n.get("mitre"):
        hugin_by_mitre.setdefault(n["mitre"], []).append(n)

# Ejemplo: ¿qué técnicas Hugin mapean a T1055 (Process Injection)?
for n in hugin_by_mitre.get("T1055", []):
    print(n["id"], n["label"])
    print(contents[n["id"]][:500])
    print("---")
```

### 7.4 Receta D — "Detectar gaps de cobertura investigables"

```python
gaps = [n for n in nodes if n.get("type") == "coverage-gap"]
print(f"Coverage gaps: {len(gaps)}")  # 149
# Cada gap es una oportunidad de investigación original
for gap in gaps[:20]:
    print(gap["id"], "→", gap["label"])
    print(contents[gap["id"]][:300])
    print("---")
```

### 7.5 Receta E — "Seguir la cadena de_GL_M expand-cards"

```bash
# 1. Ver el prompt que genera nuevas tarjetas
cat Hugin/prompts/glm-expand-cluster-to-card.md

# 2. Replicar el patrón: dado un cluster LGTM + evidence, generar una T-NNN
# El prompt pide:
#   - YAML frontmatter con id, title, category, tier, tags, mitre, ...
#   - Body con: Summary, Technical Deep Dive (500-2000 palabras),
#     Evidence, Detection & Mitigation, Related Techniques, References
#   - Lenguaje: rust, c, asm, powershell
#   - Estilo: abstracto (no mencionar source repos por nombre)
```

### 7.6 Receta F — "Auditar el grafo con Mercury (PR #22)"

El PR #22 añade un pipeline offline **Mercury** que audita cada entidad del
grafo con:

- clasificación content-type
- summary grounded (≤100 palabras)
- technical tags y entidades extraídas
- candidatos MITRE ATT&CK con confidence + evidence
- candidatos de relación con confidence + evidence
- quality issues + review metadata

Para replicarlo localmente:

```bash
# Requiere INCEPTION_API_KEY en secrets
gh workflow run "Mercury graph audit" -f limit=10
# Output: workflow artifact con structured JSON para revisión humana
```

---

## 8. PRs destacados y qué enseñan

| PR | Tema | Qué aprender de él |
|----|------|---------------------|
| #1 | HUGIN 2.0 — static knowledge universe | Cómo se diseña un vault estático: Astro + React islands + Sigma.js + Pagefind. Anonimización obligatoria. |
| #7 | Graph-first HUGIN 2.1 con quality-gated evidence | Cómo separar capas: knowledge / source / evidence / quarantine. Visual system ink+parchment+violet+red. |
| #10 | Enrich knowledge cards + source code | Cómo enrutar schemas deterministas localmente vs LLM cloud para ambigüedades. Preserva `asm, cpp, md, rs, go, nim`. |
| #14 | Graph polish + complete MITRE mapping | Cómo consumir ATT&CK Enterprise v19.1 como fuente de verdad canónica. 31/31 corpus IDs mapeados. |
| #15 | Local Gravity Lab simulation | Worker-based Verlet physics + Barnes-Hut + spatial hashing para simular el grafo sin mutar source. |
| #16 | Hydration stabilize across locales | SSR locale fix — lección sobre i18n en Astro islands. |
| #17 | Fix silent Qwen degradation + repair affected cards | Modelos locales ONNX pueden degradar silenciosamente. Cómo detectar y reparar registros afectados. |
| #19 | Add 20 new technique cards (fallback) | Cuando GLM-5.2 hace timeout, fallback procedural para generar T-NNN desde LGTM clusters. |
| #22 | Mercury graph audit pipeline | Cómo auditar entidades con closed taxonomies + strict JSON Schema + few-shot. |
| #23 | Persist stratified Mercury cleanup state | Cómo estratificar cleanup state para reanudar auditorías largas. |
| #24 | Expand batch limits for gaps process | Tunning de batch size para expand-cards. |

---

## 9. Convertir el conocimiento de Hugin en una Skill Claude/Z

Dado que el usuario quiere **enseñar a navegar Hugin para extraer cosas de
bajo nivel de Rust + red team**, la forma más útil de entregar esto es como
**un conjunto de Skills** que un agente pueda cargar bajo demanda.

### 9.1 Skills generadas a partir de Hugin

En `/home/z/my-project/download/hugin-skills/` encontrarás 58 skills
(una por técnica T-NNN), organizadas en categorías:

```
Skills/
├── syscalls/
│   ├── recycledgate-indirect-syscalls/SKILL.md      (T-001)
│   ├── hells-gate-halo-tartarus-freshycalls/SKILL.md (T-002)
│   ├── veh-syscall-gate/SKILL.md                     (T-003)
│   ├── peb-walker-gs-0x60/SKILL.md                   (T-004)
│   ├── phantom-stubs-mem-image-syscall/SKILL.md     (T-006)
│   ├── heavens-gate-32-64-bit/SKILL.md               (T-049)
│   └── manual-getprocaddress-export-walk/SKILL.md   (T-050)
├── process-injection/
│   ├── pool-party-injection/SKILL.md                 (T-007)
│   ├── threadless-injection-export-hijack/SKILL.md   (T-008)
│   ├── process-ghosting/SKILL.md                     (T-009)
│   ├── process-herpaderping/SKILL.md                 (T-010)
│   ├── dirty-vanity-reflection/SKILL.md              (T-011)
│   ├── early-cascade-apc-injection/SKILL.md          (T-012)
│   ├── ntcreateuserprocess-direct/SKILL.md           (T-014)
│   ├── ppid-spoofing/SKILL.md                        (T-015)
│   ├── manual-pe-loader-srdi/SKILL.md                (T-046)
│   └── cross-session-wts-injection/SKILL.md          (T-047)
├── edr-evasion/
│   ├── edr-evasion-suite/SKILL.md                    (T-016)
│   ├── inline-hook-implementation/SKILL.md           (T-030)
│   └── wldpquerydynamiccodetrust-check/SKILL.md      (T-031)
├── sleep-obfuscation/
│   └── ekko-rop-sleep/SKILL.md                       (T-005)
├── persistence/
│   ├── five-layer-resilience-monitor/SKILL.md        (T-017)
│   ├── edo-tensei-polymorphic-resurrection/SKILL.md  (T-018)
│   ├── ifeo-globalflag-silentprocessexit/SKILL.md    (T-034)
│   ├── port-monitor-print-spooler/SKILL.md           (T-035)
│   ├── windows-service-scm/SKILL.md                  (T-036)
│   ├── wmi-permanent-subscription/SKILL.md           (T-037)
│   ├── appinit-dlls-registry/SKILL.md                (T-038)
│   ├── on-disk-binary-patching/SKILL.md              (T-039)
│   ├── service-failure-actions-crash/SKILL.md        (T-040)
│   └── service-hiding-scm-enum/SKILL.md              (T-041)
├── networking/
│   ├── edo-dead-drop-autonomous-c2/SKILL.md          (T-019)
│   ├── network-protocol-suite/SKILL.md               (T-022)
│   ├── c2-checkin-beaconing/SKILL.md                 (T-032)
│   └── named-pipes-c2-ipc/SKILL.md                   (T-033)
├── discovery/
│   ├── host-survey-situational-awareness/SKILL.md    (T-024)
│   ├── custom-recon-lotl-api/SKILL.md                (T-025)
│   ├── dpapi-master-key-extraction/SKILL.md          (T-026)
│   ├── kuser-shared-data-direct-read/SKILL.md        (T-027)
│   ├── patch-hotfix-enum/SKILL.md                    (T-028)
│   └── sddl-security-descriptor-recon/SKILL.md       (T-029)
├── privesc/
│   ├── sebackup-restore-privilege-acl-bypass/SKILL.md (T-042)
│   ├── token-theft-tokenthief/SKILL.md                (T-043)
│   ├── service-based-lpe-scm-enum/SKILL.md            (T-044)
│   └── sedebugprivilege-abuse/SKILL.md                (T-045)
├── anti-analysis/
│   └── anti-analysis-suite/SKILL.md                  (T-020)
├── crypto/
│   └── cryptography-and-obfuscation/SKILL.md         (T-021)
└── client/
    └── client-capabilities-suite/SKILL.md            (T-023)
```

Cada `SKILL.md` tiene frontmatter `claude-red`-compatible:

```yaml
---
name: recycledgate-indirect-syscalls
description: "RecycledGate indirect syscalls technique — Rust + Red Team low-level tradecraft extracted from the Hugin knowledge graph. Covers SSN-by-RVA-sort without reading stub bodies, syscall;ret gadget hunting in ntdll, shadow-space-free jmp r11 dispatch, ETW-TI stack-walk evasion. Includes Rust asm! blocks, crate::recycled::invoke dispatcher, and lines_of_interest from dark_crystal/crowd/src/recycled.rs."
---
```

Más un skill meta:

```
Skills/utility/
└── navigate-hugin-graph/SKILL.md   # El presente documento, como skill
```

### 9.2 Cómo cargar estas skills en Claude Code

```bash
# Una sola skill
cat Skills/syscalls/recycledgate-indirect-syscalls/SKILL.md | claude --system-file -

# Toda una categoría
cat Skills/process-injection/**/SKILL.md | claude --system-file -

# Todas a la vez (heavy context)
find Skills -name SKILL.md -exec cat {} + | claude --system-file -
```

### 9.3 Cómo usarlas en Claude.ai (Projects)

Pega el contenido de las skills relevantes en el system prompt del Project.
Para proyectos de red team Rust, recomendadas como base:

- `recycledgate-indirect-syscalls` + `hells-gate-halo-tartarus-freshycalls`
- `pool-party-injection` + `early-cascade-apc-injection`
- `ekko-rop-sleep`
- `five-layer-resilience-monitor`
- `navigate-hugin-graph` (esta guía)

---

## 10. Plan de uso recomendado

1. **Lectura inicial (30 min):** lee `hugin-knowledge-map.json` para ver los números totales y el inventario.
2. **Inmersión (2 h):** abre `hugin-technique-cards.md` y lee las técnicas tier-S primero (T-001, T-002, T-003, T-005, T-007, T-009, T-012, T-014, T-015, T-017, T-018, T-019, T-042).
3. **Cross-reference (1 h):** para cada técnica tier-S, abre su playbook en `hugin-playbooks.md` y anota los archivos Rust referenciados.
4. **Navegación web (30 min):** abre <https://princeofpwn.github.io/Hugin/graph/> y navega las técnicas visualmente. Click en cada nodo → panel con detalles.
5. **Carga en agente:** copia las skills relevantes a tu `~/.claude/skills/` o pásalas como `--system-file`.
6. **Iteración:** cada semana, `git pull` en el repo Hugin para recibir nuevas técnicas generadas por `expand-cards.yml`. Vuelve a ejecutar `scripts/extract_hugin_knowledge.py` para regenerar los digests.
7. **Contribución:** si descubres un coverage-gap interesante, replica el patrón del prompt `prompts/glm-expand-cluster-to-card.md` y propón una nueva tarjeta vía PR.

---

## 11. Advertencias operacionales

- **Todo en Hugin está anonimizado.** Los nombres `dark_crystal`, `crowd/`, `crates/core/`, `client_rust` son aliases. No intentes buscar esos repos en GitHub — son privados.
- **El grafo está vivo.** Cada 4h puede cambiar. Si haces análisis profundo, fecha tu extracto (`hugin-knowledge-map.json` incluye `sourceHash`).
- **No es material listo para usar en producción.** Las técnicas son **investigación** — el código Rust es educativo, no está hardening-tested.
- **Privacidad:** el `ownerAuthorization` permite al operador publicar la proyección. No la uses para atribuir ataques.
- **Legal:** el material cubre técnicas ofensivas Windows/Linux. Úsalo solo en engagements autorizados, research, o CTF.

---

## 12. Próximos pasos sugeridos

- **Iterar las skills:** cada skill actual es un extracto crudo. Puedes enriquecerlas con ejemplos de payloads propios, links a CVEs concretos, o adaptaciones a tu stack.
- **Fork de Hugin:** si quieres tu propio vault, forkea el repo, reemplaza `data/incoming/` con tu material crudo, y ejecuta el pipeline.
- **Extender el prompt GLM:** edita `prompts/glm-expand-cluster-to-card.md` para añadir foco en una familia concreta (ej. Rust async implants, kernel drivers, eBPF).
- **Contribuir coverage-gaps:** los 149 gaps son oportunidades originales. Genera tarjetas y abre PR siguiendo el formato del prompt.
- **Integrar con Munin:** si tienes acceso al harvester upstream, puedes cerrar el loop end-to-end: Munin recolecta → Hugin proyecta → skills alimentan a tu agente → agente genera nuevo conocimiento → vuelve a Munin.

---

**Fin de la guía.** El conocimiento está en los archivos — esta guía solo enseña a leerlos.
