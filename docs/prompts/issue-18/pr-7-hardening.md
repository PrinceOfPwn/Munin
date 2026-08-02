# Prompt PR-7 — Production Hardening: Accesibilidad, Navegación por Teclado, Virtualización y Streaming de Alto Volumen

> Issue: #18 · Fase 7 · Ola 3 · **Requiere PR-3, PR-4, PR-5 y PR-6 en `main`**
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Eres un Ingeniero QA & Performance Frontend Senior. Tu tarea es pulir la interfaz reconstruida en las fases anteriores para asegurar estándares de producción: accesibilidad (a11y), atajos de teclado, rendimiento con 1,000+ eventos en vivo y pruebas de estres de streaming de alto volumen.

---

## 2. Rutas que SOLO este PR modifica (Rutas Permitidas)

PUEDES crear o editar ÚNICAMENTE estas rutas:
- `app/src/components/**` (Ajustes de accesibilidad, aria-labels, atajos de teclado y rendimiento)
- `app/src/lib/__tests__/high_volume_streaming.test.ts` (NUEVO)
- `app/src/lib/__tests__/a11y_keyboard.test.ts` (NUEVO)
- `docs/issue-18-hardening.md` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `munin/**` (Salvo que requieras un fixture de test)

---

## 3. Especificación detallada paso a paso

### Paso 3.1: Accesibilidad y Atajos de Teclado

1. **Focus Rings Visibles**: Asegúrate de que todos los elementos interactivos tengan la clase `:focus-visible` activa con el anillo violeta de la paleta (`ring-2 ring-accent`).
2. **Atajos de Teclado Globales**:
   - `Cmd+Enter` / `Ctrl+Enter`: Enviar mensaje en el composer.
   - `Escape`: Cerrar modales, vistas en pantalla completa de terminal y drawers del workspace.
   - `Cmd+K` / `Ctrl+K`: Enfocar la barra de búsqueda en el sidebar de operaciones.
3. **Atributos Aria**: Añade `aria-label`, `role="region"`, `role="dialog"`, `aria-expanded` en botones colapsables y drawers.

### Paso 3.2: Rendimiento y Virtualización de Mensajes

1. Si una conversación contiene más de 100 partes de mensaje o logs extensos, la interfaz NO debe sufrir caídas de cuadros por segundo (FPS).
2. Utiliza renderizado eficiente en listas de conversación evitando recalcular posiciones del DOM innecesarias durante el streaming.

### Paso 3.3: Pruebas de Estrés de Streaming de Alto Volumen

Crea `app/src/lib/__tests__/high_volume_streaming.test.ts`:
1. Simula el consumo de 5,000 paquetes `UIMessageChunk` en menos de 2 segundos.
2. Verifica que la memoria del estado de React/Zustand se mantenga acotada.
3. Verifica que la reconciliación por ID estable no duplique elementos en el array.

---

## 4. Verificación Obligatoria

```bash
cd app
npm run lint
npm run typecheck
npm run build
npm test
```

---

## 5. Instrucciones de Commit y PR

- Rama: `feat/issue-18-7-hardening`
- Commit: `feat(issue-18-7): add keyboard navigation, a11y focus rings, and high-volume streaming stress tests`
- Abre el PR contra `main`.
