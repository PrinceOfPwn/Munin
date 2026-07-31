"use client";

/**
 * Floating, draggable, resizable, non-modal window rendered into a portal on
 * `document.body`.  Built with native pointer events so we don't add a
 * dependency for something this small.
 *
 * Persistence
 * -----------
 * Position + size + minimised state are stored under
 * `munin.window.<id>` in localStorage.  A window whose `id` is not persisted
 * yet uses `defaultPosition`/`defaultSize`; otherwise the stored values win.
 *
 * z-order
 * -------
 * A module-level monotonically-increasing counter (`bringToFront`) assigns a
 * fresh z-index on every window mount + every user interaction, so the last-
 * touched window always sits on top.  This means N floating chats stay
 * independently orderable without a global "focus" state machine.
 *
 * Accessibility
 * -------------
 * `role="dialog"`, `aria-label={title}`, and a labelled close button.  Focus
 * is NOT trapped — the window is deliberately non-modal, and the operator can
 * click through to the chat behind it.
 */
import * as React from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minimize2, X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface FloatingWindowProps {
  id: string;
  title: string;
  icon?: React.ReactNode;
  onClose: () => void;
  defaultPosition?: { x: number; y: number };
  defaultSize?: { width: number; height: number };
  minSize?: { width: number; height: number };
  maxSize?: { width: number; height: number };
  children: React.ReactNode;
  /** Rendered inside the header, after the title.  Use for status chips. */
  headerRight?: React.ReactNode;
  className?: string;
}

interface WindowState {
  x: number;
  y: number;
  width: number;
  height: number;
  minimized: boolean;
}

const DEFAULT_MIN = { width: 340, height: 260 };
const DEFAULT_MAX = { width: 900, height: 720 };

// Module-level z-order stack.  Base is Tailwind's `z-floating` (=40).  Each
// window gets a fresh z-index above its siblings but strictly below the modal
// tier (=60) so an AlertDialog HITL confirm always overlays open windows.
// Cap at 59 so 20 stacked windows still stay under the modal ceiling.
const FLOATING_BASE = 40;
const FLOATING_CEILING = 59;
let __zStackTop = FLOATING_BASE;
function bringToFront(): number {
  __zStackTop = __zStackTop >= FLOATING_CEILING ? FLOATING_CEILING : __zStackTop + 1;
  return __zStackTop;
}

function storageKey(id: string): string {
  return `munin.window.${id}`;
}

function loadState(id: string, fallback: WindowState): WindowState {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(storageKey(id));
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<WindowState>;
    return {
      x: typeof parsed.x === "number" ? parsed.x : fallback.x,
      y: typeof parsed.y === "number" ? parsed.y : fallback.y,
      width: typeof parsed.width === "number" ? parsed.width : fallback.width,
      height: typeof parsed.height === "number" ? parsed.height : fallback.height,
      minimized: Boolean(parsed.minimized),
    };
  } catch {
    return fallback;
  }
}

function saveState(id: string, state: WindowState): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey(id), JSON.stringify(state));
  } catch {
    /* ignore quota errors */
  }
}

export function FloatingWindow({
  id,
  title,
  icon,
  onClose,
  defaultPosition,
  defaultSize,
  minSize = DEFAULT_MIN,
  maxSize = DEFAULT_MAX,
  children,
  headerRight,
  className,
}: FloatingWindowProps) {
  const [mounted, setMounted] = React.useState(false);
  const [state, setState] = React.useState<WindowState>(() =>
    loadState(id, {
      x: defaultPosition?.x ?? 120,
      y: defaultPosition?.y ?? 120,
      width: defaultSize?.width ?? 480,
      height: defaultSize?.height ?? 400,
      minimized: false,
    }),
  );
  const [zIndex, setZIndex] = React.useState<number>(() => bringToFront());
  const dragRef = React.useRef<{
    startX: number;
    startY: number;
    winX: number;
    winY: number;
  } | null>(null);
  const resizeRef = React.useRef<{
    startX: number;
    startY: number;
    width: number;
    height: number;
  } | null>(null);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  React.useEffect(() => {
    saveState(id, state);
  }, [id, state]);

  const focus = React.useCallback(() => setZIndex(bringToFront()), []);

  const onDragPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    (event.target as HTMLElement).setPointerCapture(event.pointerId);
    focus();
    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      winX: state.x,
      winY: state.y,
    };
  };
  const onDragPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    const dx = event.clientX - dragRef.current.startX;
    const dy = event.clientY - dragRef.current.startY;
    setState((prev) => ({
      ...prev,
      x: Math.max(0, dragRef.current!.winX + dx),
      y: Math.max(0, dragRef.current!.winY + dy),
    }));
  };
  const onDragPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current) {
      dragRef.current = null;
      (event.target as HTMLElement).releasePointerCapture(event.pointerId);
    }
  };

  const onResizePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    (event.target as HTMLElement).setPointerCapture(event.pointerId);
    focus();
    resizeRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      width: state.width,
      height: state.height,
    };
  };
  const onResizePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!resizeRef.current) return;
    const dx = event.clientX - resizeRef.current.startX;
    const dy = event.clientY - resizeRef.current.startY;
    const w = Math.min(
      maxSize.width,
      Math.max(minSize.width, resizeRef.current.width + dx),
    );
    const h = Math.min(
      maxSize.height,
      Math.max(minSize.height, resizeRef.current.height + dy),
    );
    setState((prev) => ({ ...prev, width: w, height: h }));
  };
  const onResizePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (resizeRef.current) {
      resizeRef.current = null;
      (event.target as HTMLElement).releasePointerCapture(event.pointerId);
    }
  };

  const toggleMinimized = () =>
    setState((prev) => ({ ...prev, minimized: !prev.minimized }));

  if (!mounted) return null;

  const style: React.CSSProperties = {
    position: "fixed",
    left: state.x,
    top: state.y,
    width: state.width,
    height: state.minimized ? 40 : state.height,
    zIndex,
  };

  return createPortal(
    <div
      role="dialog"
      aria-label={title}
      style={style}
      onPointerDown={focus}
      className={cn(
        "flex flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-2xl",
        className,
      )}
    >
      <div
        onPointerDown={onDragPointerDown}
        onPointerMove={onDragPointerMove}
        onPointerUp={onDragPointerUp}
        onPointerCancel={onDragPointerUp}
        className="flex h-10 cursor-move select-none items-center gap-2 border-b border-border bg-raised px-3"
      >
        {icon && <span className="text-secondary">{icon}</span>}
        <span className="truncate text-xs font-mono font-semibold text-body">
          {title}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          {headerRight}
          <button
            type="button"
            onClick={toggleMinimized}
            aria-label={state.minimized ? "Restore" : "Minimize"}
            className="rounded p-1 text-muted transition-colors hover:bg-bg hover:text-body"
          >
            {state.minimized ? (
              <Maximize2 className="h-3.5 w-3.5" />
            ) : (
              <Minimize2 className="h-3.5 w-3.5" />
            )}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close window"
            className="rounded p-1 text-muted transition-colors hover:bg-danger/10 hover:text-danger"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {!state.minimized && (
        <>
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {children}
          </div>
          <div
            onPointerDown={onResizePointerDown}
            onPointerMove={onResizePointerMove}
            onPointerUp={onResizePointerUp}
            onPointerCancel={onResizePointerUp}
            className="absolute bottom-0 right-0 h-4 w-4 cursor-nwse-resize"
            aria-hidden
            style={{
              backgroundImage:
                "linear-gradient(135deg, transparent 0 45%, rgba(200,200,200,0.35) 45% 55%, transparent 55% 100%)",
            }}
          />
        </>
      )}
    </div>,
    document.body,
  );
}
