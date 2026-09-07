import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import CameraPage from "@/app/(panel)/camara/page";
import { HomeContext } from "@/components/home-context";

const home = { id: "home-a", name: "Casa", timezone: "Asia/Tokyo", role: "operator", permissions: ["devices.read", "devices.control", "events.read", "camera.view"] };
const device = { id: "device-a", home_id: home.id, device_id: "PI-000001", name: "Entrada", status: "online", last_seen_at: "2026-09-05T12:00:00Z", created_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-05T12:00:00Z" };
let eventCalls = 0;
let terminalStatus: "completed" | "failed" | "timeout" = "completed";
let captureFailures = 0;

beforeEach(() => {
  eventCalls = 0;
  terminalStatus = "completed";
  captureFailures = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET";
    if (url.endsWith("/devices")) return Response.json({ items: [device] });
    if (method === "POST" && url.endsWith("/commands")) {
      if (captureFailures > 0) { captureFailures -= 1; return Response.json({ detail: "Unavailable" }, { status: 503 }); }
      return Response.json({ command_id: "command-new", device_id: device.id, requested_by_user_id: "user-a", command: "camera.capture", payload: {}, status: "pending", result: null, created_at: "2026-09-05T12:01:00Z", timeline: [{ status: "pending", at: "2026-09-05T12:01:00Z" }] }, { status: 201 });
    }
    if (url.endsWith("/commands/command-new")) return Response.json({ command_id: "command-new", device_id: device.id, requested_by_user_id: "user-a", command: "camera.capture", payload: {}, status: terminalStatus, result: terminalStatus === "completed" ? {} : { error: "camera unavailable" }, created_at: "2026-09-05T12:01:00Z", timeline: [{ status: "pending", at: "2026-09-05T12:01:00Z" }, { status: terminalStatus, at: "2026-09-05T12:01:02Z" }] });
    if (url.includes("/events?")) {
      eventCalls += 1;
      const media = eventCalls > 1 ? { id: "event-new", home_id: home.id, device_id: device.id, event_type: "camera_capture", created_at: "2026-09-05T12:01:03Z", payload: { command_id: "command-new", media_id: "media-new" } } : { id: "event-old", home_id: home.id, device_id: device.id, event_type: "camera_capture", created_at: "2026-09-05T12:00:00Z", payload: { command_id: "command-old", media_id: "media-old" } };
      return Response.json({ items: [media], next_cursor: null });
    }
    throw new Error(`Unexpected request: ${method} ${url}`);
  }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("reloads the durable latest media after a confirmed capture and shows the terminal command timeline", async () => {
  const user = userEvent.setup();
  render(<HomeContext.Provider value={home}><CameraPage /></HomeContext.Provider>);
  expect(await screen.findByRole("img", { name: /última captura de entrada/i })).toHaveAttribute("src", "/api/homes/home-a/media/media-old");
  expect(screen.getByText(/capturada el/i)).toHaveTextContent("21:00:00");
  await user.click(screen.getByRole("button", { name: "Capturar imagen" }));
  expect(screen.getByRole("dialog")).toHaveTextContent(/capturará una imagen/i);
  await user.click(screen.getByRole("button", { name: "Confirmar captura" }));
  expect(await screen.findByText("Completado", { selector: ".command-timeline span" })).toBeVisible();
  expect(screen.getByRole("region", { name: "Progreso del comando" })).toHaveTextContent("21:01:02");
  expect(await screen.findByRole("img", { name: /última captura de entrada/i })).toHaveAttribute("src", "/api/homes/home-a/media/media-new");
  expect(fetch).toHaveBeenCalledWith("/api/homes/home-a/devices/device-a/commands", expect.objectContaining({ method: "POST", body: JSON.stringify({ command: "camera.capture", payload: {} }) }));
  expect(fetch).toHaveBeenCalledWith("/api/homes/home-a/events?event_type=camera_capture&device_id=device-a&limit=1", expect.anything());
});

it("renders honest permission and empty-device states without issuing forbidden requests", () => {
  vi.mocked(fetch).mockClear();
  render(<HomeContext.Provider value={{ ...home, permissions: [] }}><CameraPage /></HomeContext.Provider>);
  expect(screen.getByText(/sin permiso para ver la cámara/i)).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});

it("announces capture errors inside the open confirmation and allows retry", async () => {
  captureFailures = 1;
  const user = userEvent.setup();
  render(<HomeContext.Provider value={home}><CameraPage /></HomeContext.Provider>);
  await screen.findByRole("img", { name: /última captura de entrada/i });
  await user.click(screen.getByRole("button", { name: "Capturar imagen" }));
  const dialog = screen.getByRole("dialog", { name: "Capturar imagen con Entrada" });
  await user.click(within(dialog).getByRole("button", { name: "Confirmar captura" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(/no pudimos iniciar la captura/i);
  expect(within(dialog).getByRole("button", { name: "Cancelar" })).toBeEnabled();
  await user.click(within(dialog).getByRole("button", { name: "Confirmar captura" }));
  expect(screen.queryByRole("dialog", { name: "Capturar imagen con Entrada" })).not.toBeInTheDocument();
  expect(await screen.findByText("Completado", { selector: ".command-timeline span" })).toBeVisible();
});

it.each([
  ["failed", "Fallido"],
  ["timeout", "Tiempo de espera agotado"],
] as const)("does not discover media after a %s capture command", async (status, label) => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  terminalStatus = status;
  const user = userEvent.setup();
  render(<HomeContext.Provider value={home}><CameraPage /></HomeContext.Provider>);
  await screen.findByRole("img", { name: /última captura de entrada/i });
  const initialEventCalls = eventCalls;
  await user.click(screen.getByRole("button", { name: "Capturar imagen" }));
  await user.click(screen.getByRole("button", { name: "Confirmar captura" }));
  expect(await screen.findByText(label, { selector: ".command-timeline span" })).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(screen.queryByText(/esperando que la imagen/i)).not.toBeInTheDocument();
  expect(eventCalls).toBe(initialEventCalls);
});
