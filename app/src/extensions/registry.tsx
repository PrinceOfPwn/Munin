"use client";

import dynamic from "next/dynamic";
import type { ComponentType } from "react";

export type ExtensionSlot = "command_center" | "conversation_inspector" | "run_timeline" | "settings";
export type ExtensionPermission = "read:conversation" | "read:run" | "read:artifact" | "propose:diff";
export type ExtensionManifest = { id: string; version: string; slots: ExtensionSlot[]; permissions: ExtensionPermission[]; featureFlag: string; entrypoint: string };
export type Widget = { manifest: ExtensionManifest; Component: ComponentType };

const permittedSlots: ExtensionSlot[] = ["command_center", "conversation_inspector", "run_timeline", "settings"];

export function registerWidget(manifest: ExtensionManifest): Widget {
  if (!manifest.id || manifest.entrypoint.startsWith("http") || manifest.slots.some((slot) => !permittedSlots.includes(slot))) throw new Error("Invalid isolated Munin extension manifest");
  return { manifest, Component: dynamic(() => import(manifest.entrypoint), { ssr: false, loading: () => null }) };
}

export function enabledWidgets(registry: Widget[], slot: ExtensionSlot, flags: Record<string, boolean>) {
  return registry.filter((widget) => widget.manifest.slots.includes(slot) && flags[widget.manifest.featureFlag]);
}
