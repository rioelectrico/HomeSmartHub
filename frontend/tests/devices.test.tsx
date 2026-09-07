import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DevicesPage from "@/app/(panel)/dispositivos/page";
import { HomeContext } from "@/components/home-context";
import type { Device, ProvisionedDevice } from "@/types/api";

const home = { id: "home-a", name: "Casa", timezone: "UTC", role: "administrator", permissions: ["devices.read", "devices.manage", "devices.control"] };
const baseDevice: Device = { id: "device-a", home_id: home.id, device_id: "PI-000001", name: "Entrada", status: "online", last_seen_at: "2026-09-05T12:00:00Z", created_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-05T12:00:00Z" };
let devices: Device[] = [baseDevice];
let calls: { url: string; method: string; body?: unknown }[];

function renderPage(permissions = home.permissions, timezone = home.timezone) {
  return render(<HomeContext.Provider value={{ ...home, permissions, timezone }}><DevicesPage /></HomeContext.Provider>);
}

beforeEach(() => {
  devices = [baseDevice];
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET", body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (method === "GET") return Response.json({ items: devices });
    if (method === "POST" && url.endsWith("/devices")) {
      const created: ProvisionedDevice = { ...baseDevice, id: "device-new", ...(body as Pick<Device, "device_id" | "name">), status: "provisioning", last_seen_at: null, secret: "one-time-device-secret-1234567890" };
      devices = [...devices, created];
      return Response.json(created, { status: 201 });
    }
    if (method === "POST" && url.endsWith("/commands")) return Response.json({ command_id: "command-status", device_id: "device-a", requested_by_user_id: "user-a", command: "device.status.request", payload: {}, status: "completed", result: {}, created_at: "2026-09-05T12:01:00Z", timeline: [{ status: "pending", at: "2026-09-05T12:01:00Z" }, { status: "completed", at: "2026-09-05T12:01:01Z" }] }, { status: 201 });
    const status = url.endsWith("/disable") ? "disabled" : "offline";
    devices = devices.map((device) => device.id === "device-a" ? { ...device, status } : device);
    return Response.json({ ...baseDevice, status });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("Dispositivos", () => {
  it("shows durable device state, connection time, and bounded pagination", async () => {
    devices = Array.from({ length: 11 }, (_, index) => ({ ...baseDevice, id: `device-${index}`, device_id: `PI-${String(index + 1).padStart(6, "0")}`, name: `Acceso ${index + 1}`, status: index === 0 ? "online" : "offline" }));
    renderPage(["devices.read"]);
    expect(await screen.findByText("Acceso 1")).toBeVisible();
    expect(screen.getByText("En línea")).toBeVisible();
    expect(screen.getAllByText(/5\/9\/2026|05\/09\/2026/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Acceso 11")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Siguiente" }));
    expect(screen.getByText("Acceso 11")).toBeVisible();
  });

  it("formats device and command timestamps in the selected home timezone", async () => {
    renderPage(home.permissions, "Asia/Tokyo");
    expect(await screen.findByText("Entrada")).toBeVisible();
    expect(screen.getByText(/21:00:00/, { selector: "time" })).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Solicitar estado de Entrada" }));
    const timeline = await screen.findByRole("region", { name: "Progreso del comando" });
    expect(within(timeline).getByText(/21:01:01/, { selector: "time" })).toBeVisible();
  });

  it("shows a provisioned secret once and requires explicit acknowledgement before closing", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Entrada");
    await user.click(screen.getByRole("button", { name: "Aprovisionar dispositivo" }));
    const dialog = screen.getByRole("dialog", { name: "Aprovisionar dispositivo" });
    await user.type(within(dialog).getByLabelText("Identificador del dispositivo"), "PI-000002");
    await user.type(within(dialog).getByLabelText("Nombre del dispositivo"), "Cochera");
    await user.click(within(dialog).getByRole("button", { name: "Aprovisionar" }));
    expect(await screen.findByText(/guardalo ahora/i)).toBeVisible();
    expect(screen.getByText("one-time-device-secret-1234567890")).toBeVisible();
    expect(screen.getByRole("button", { name: "Cerrar" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeVisible();
    await user.click(screen.getByRole("checkbox", { name: /guardé el secreto/i }));
    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByText("one-time-device-secret-1234567890")).not.toBeInTheDocument();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({ device_id: "PI-000002", name: "Cochera" });
  });

  it("confirms lifecycle changes and never exposes management actions without permission", async () => {
    const user = userEvent.setup();
    const view = renderPage();
    await screen.findByText("Entrada");
    await user.click(screen.getByRole("button", { name: "Deshabilitar Entrada" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(/dejará de autenticarse/i);
    await user.click(screen.getByRole("button", { name: "Confirmar deshabilitación" }));
    expect(await screen.findByText("Deshabilitado")).toBeVisible();
    expect(calls.some((call) => call.url.endsWith("/devices/device-a/disable") && call.method === "POST")).toBe(true);
    view.unmount();
    renderPage(["devices.read"]);
    await screen.findByText("Entrada");
    expect(screen.queryByRole("button", { name: /aprovisionar|deshabilitar|rotar/i })).not.toBeInTheDocument();
  });

  it("announces lifecycle errors inside the open confirmation and allows retry", async () => {
    let attempts = 0;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input), method = init?.method ?? "GET";
      if (method === "GET" && url.endsWith("/devices")) return Response.json({ items: devices });
      if (method === "POST" && url.endsWith("/devices/device-a/disable")) {
        attempts += 1;
        if (attempts === 1) return Response.json({ detail: "Unavailable" }, { status: 503 });
        devices = [{ ...baseDevice, status: "disabled" }];
        return Response.json(devices[0]);
      }
      throw new Error(`Unexpected request: ${method} ${url}`);
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Entrada");
    await user.click(screen.getByRole("button", { name: "Deshabilitar Entrada" }));
    const dialog = screen.getByRole("dialog", { name: "Deshabilitar Entrada" });
    await user.click(within(dialog).getByRole("button", { name: "Confirmar deshabilitación" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("No pudimos actualizar el dispositivo.");
    expect(within(dialog).getByRole("button", { name: "Cancelar" })).toBeEnabled();
    await user.click(within(dialog).getByRole("button", { name: "Confirmar deshabilitación" }));
    expect(await screen.findByText("Dispositivo deshabilitado.")).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "Deshabilitar Entrada" })).not.toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("shows terminal command progress when requesting a device status", async () => {
    renderPage();
    await screen.findByText("Entrada");
    await userEvent.setup().click(screen.getByRole("button", { name: "Solicitar estado de Entrada" }));
    expect(await screen.findByRole("region", { name: "Progreso del comando" })).toHaveTextContent(/Estado actual:\s*Completado/i);
    expect(calls.some((call) => call.url.endsWith("/devices/device-a/commands") && call.method === "POST" && JSON.stringify(call.body) === JSON.stringify({ command: "device.status.request", payload: {} }))).toBe(true);
  });

  it("keeps a status command bound to its owner while another device action opens", async () => {
    const secondDevice = { ...baseDevice, id: "device-b", device_id: "PI-000002", name: "Cochera" };
    devices = [baseDevice, secondDevice];
    let finishCommand: (() => void) | undefined;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input), method = init?.method ?? "GET";
      calls.push({ url, method });
      if (method === "GET" && url.endsWith("/devices")) return Response.json({ items: devices });
      if (method === "POST" && url.endsWith("/devices/device-a/commands")) return Response.json({ command_id: "command-status", device_id: "device-a", requested_by_user_id: "user-a", command: "device.status.request", payload: {}, status: "pending", result: null, created_at: "2026-09-05T12:01:00Z", timeline: [{ status: "pending", at: "2026-09-05T12:01:00Z" }] }, { status: 201 });
      if (method === "GET" && url.endsWith("/devices/device-a/commands/command-status")) return new Promise<Response>((resolve) => { finishCommand = () => resolve(Response.json({ command_id: "command-status", device_id: "device-a", requested_by_user_id: "user-a", command: "device.status.request", payload: {}, status: "completed", result: {}, created_at: "2026-09-05T12:01:00Z", timeline: [{ status: "pending", at: "2026-09-05T12:01:00Z" }, { status: "completed", at: "2026-09-05T12:01:01Z" }] })); });
      throw new Error(`Unexpected request: ${method} ${url}`);
    });

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Cochera");
    await user.click(screen.getByRole("button", { name: "Solicitar estado de Entrada" }));
    const timeline = await screen.findByRole("region", { name: "Progreso del comando" });
    await waitFor(() => expect(finishCommand).toBeTypeOf("function"));
    await user.click(screen.getByRole("button", { name: "Rotar secreto de Cochera" }));
    expect(screen.getByRole("dialog", { name: "Rotar secreto de Cochera" })).toBeVisible();
    await act(async () => finishCommand?.());
    await waitFor(() => expect(timeline).toHaveTextContent(/Estado actual:\s*Completado/i));
    expect(calls.filter((call) => call.url.includes("/commands/command-status")).map((call) => call.url)).toEqual(["/api/homes/home-a/devices/device-a/commands/command-status"]);
  });
});
