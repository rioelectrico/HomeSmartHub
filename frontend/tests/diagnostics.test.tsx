import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import DiagnosticsPage from "@/app/(panel)/diagnostico/page";
import ConfigurationPage from "@/app/(panel)/configuracion/page";
import { HomeContext } from "@/components/home-context";
import type { Diagnostics } from "@/types/api";

const home = { id: "home-a", name: "Casa", timezone: "Asia/Tokyo", role: "administrator", permissions: ["devices.read", "devices.control"] };
const diagnostics = { services: { backend: "up", postgresql: "down", openai: "unconfigured" }, devices: { items: [{ id: "device-a", device_id: "PI-000001", name: "Entrada", status: "online", connected: true, last_seen_at: "2026-09-05T12:00:00Z", snapshot_at: "2026-09-05T12:00:00Z", snapshot: { camera: "ready" } }], next_cursor: null } };
let commandBodies: unknown[];
let cameraFailures: number;
let openaiStatus: Diagnostics["services"]["openai"];

beforeEach(() => {
  commandBodies = [];
  cameraFailures = 0;
  openaiStatus = "unconfigured";
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET";
    if (url.includes("/diagnostics")) return Response.json({ ...diagnostics, services: { ...diagnostics.services, openai: openaiStatus } });
    if (method === "POST" && url.endsWith("/commands")) {
      commandBodies.push(JSON.parse(String(init?.body)));
      const command = (commandBodies.at(-1) as { command: string }).command;
      if (command === "camera.capture" && cameraFailures > 0) { cameraFailures -= 1; return Response.json({ detail: "Unavailable" }, { status: 503 }); }
      return Response.json({ command_id: `command-${commandBodies.length}`, device_id: "device-a", requested_by_user_id: "user-a", command, payload: {}, status: "completed", result: {}, created_at: "2026-09-05T12:00:00Z", timeline: [{ status: "pending", at: "2026-09-05T12:00:00Z" }, { status: "completed", at: "2026-09-05T12:00:01Z" }] }, { status: 201 });
    }
    if (url.includes("/commands/")) throw new Error("A terminal command must not keep polling");
    throw new Error(`Unexpected request ${method} ${url}`);
  }));
});
afterEach(() => vi.unstubAllGlobals());

it.each([
  ["unconfigured", "Sin configurar", "attention"],
  ["configured", "Configurado", "ok"],
  ["available", "Disponible", "ok"],
  ["unavailable", "No disponible", "attention"],
] as const)("represents OpenAI %s accessibly without probing the external provider", async (value, label, health) => {
  openaiStatus = value;
  render(<HomeContext.Provider value={home}><DiagnosticsPage /></HomeContext.Provider>);
  const card = await screen.findByRole("article", { name: "OpenAI" });
  const state = within(card).getByRole("status");
  expect(state).toHaveTextContent(label);
  expect(state).toHaveClass(`service-state--${health}`);
  expect(screen.getByText(/configurado indica presencia de configuración, no una prueba de disponibilidad externa/i)).toBeVisible();
  expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual(["/api/homes/home-a/diagnostics?limit=20"]);
});

it("separates service health and exposes only the two permitted diagnostic actions", async () => {
  const user = userEvent.setup();
  render(<HomeContext.Provider value={home}><DiagnosticsPage /></HomeContext.Provider>);
  expect(await screen.findByRole("article", { name: "Backend" })).toHaveTextContent("Disponible");
  expect(screen.getByRole("article", { name: "PostgreSQL" })).toHaveTextContent("No disponible");
  expect(screen.getByRole("article", { name: "OpenAI" })).toHaveTextContent("Sin configurar");
  expect(screen.getAllByText(/21:00:00/, { selector: "time" })).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Solicitar estado de Entrada" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Probar cámara de Entrada" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /reiniciar|deshabilitar|rotar/i })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Solicitar estado de Entrada" }));
  expect(await screen.findByText("Completado", { selector: ".command-timeline span" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Probar cámara de Entrada" }));
  expect(screen.getByRole("dialog")).toHaveTextContent(/capturará una imagen/i);
  await user.click(screen.getByRole("button", { name: "Confirmar prueba" }));
  expect(commandBodies).toEqual([{ command: "device.status.request", payload: {} }, { command: "camera.capture", payload: {} }]);
});

it("hides controls without device control permission", async () => {
  render(<HomeContext.Provider value={{ ...home, permissions: ["devices.read"] }}><DiagnosticsPage /></HomeContext.Provider>);
  expect(await screen.findByText("Entrada")).toBeVisible();
  expect(screen.queryByRole("button", { name: /solicitar estado|probar cámara/i })).not.toBeInTheDocument();
});

it("announces camera diagnostic errors inside the open confirmation and allows retry", async () => {
  cameraFailures = 1;
  const user = userEvent.setup();
  render(<HomeContext.Provider value={home}><DiagnosticsPage /></HomeContext.Provider>);
  await screen.findByText("Entrada");
  await user.click(screen.getByRole("button", { name: "Probar cámara de Entrada" }));
  const dialog = screen.getByRole("dialog", { name: "Probar cámara de Entrada" });
  await user.click(within(dialog).getByRole("button", { name: "Confirmar prueba" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(/no pudimos enviar el comando/i);
  expect(within(dialog).getByRole("button", { name: "Cancelar" })).toBeEnabled();
  await user.click(within(dialog).getByRole("button", { name: "Confirmar prueba" }));
  expect(screen.queryByRole("dialog", { name: "Probar cámara de Entrada" })).not.toBeInTheDocument();
  expect(commandBodies).toHaveLength(2);
});

it("shows only non-secret operational configuration as read-only values", () => {
  render(<HomeContext.Provider value={home}><ConfigurationPage /></HomeContext.Provider>);
  expect(screen.getByRole("heading", { name: "Configuración" })).toBeVisible();
  expect(screen.getByText("Casa")).toBeVisible();
  expect(screen.getByText("Asia/Tokyo")).toBeVisible();
  expect(screen.getByText(/mismo origen/i)).toBeVisible();
  expect(screen.queryByText(/^(password|secret|api key)$/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});
