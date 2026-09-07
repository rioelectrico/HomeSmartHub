import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import EventsPage from "@/app/(panel)/eventos/page";
import StatisticsPage from "@/app/(panel)/estadisticas/page";
import { HomeContext } from "@/components/home-context";

const adminHome = { id: "home-a", name: "Casa", timezone: "Asia/Tokyo", role: "administrator", permissions: ["events.read", "audit.read", "devices.read"] };
const event = { id: "event-a", home_id: adminHome.id, device_id: "device-a", event_type: "device_status", created_at: "2026-09-05T12:00:00Z", payload: { camera: "ready", free_heap_bytes: 2048 } };
const audit = { id: "audit-a", home_id: adminHome.id, user_id: "user-a", action: "device.disabled", detail: "Device disabled", context: { device_id: "PI-000001" }, created_at: "2026-09-05T12:00:00Z" };
const statistics = { timezone: adminHome.timezone, generated_at: "2026-09-05T12:00:00Z", today: { events: 8, conversations: 3, captures: 2 }, last_7_days: { events: 24, conversations: 9, captures: 6 }, daily: Array.from({ length: 7 }, (_, index) => ({ date: `2026-09-0${index + 1}`, events: index + 1, conversations: index, captures: index % 2 })), devices: { total: 1, online: 1, offline: 0, disabled: 0, provisioning: 0 } };
let calls: string[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input); calls.push(url);
    if (url.endsWith("/devices")) return Response.json({ items: [{ id: "device-a", device_id: "PI-000001", name: "Entrada", home_id: adminHome.id, status: "online", last_seen_at: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" }] });
    if (url.includes("/audit")) return Response.json({ items: [audit], next_cursor: null });
    if (url.includes("/events/event-a")) return Response.json(event);
    if (url.includes("/events")) return Response.json({ items: [event], next_cursor: url.includes("cursor=") ? null : "2026-09-05T12:00:00+00:00|00000000-0000-0000-0000-000000000001" });
    if (url.endsWith("/statistics")) return Response.json(statistics);
    throw new Error(`Unexpected request ${url}`);
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("Eventos y auditoría", () => {
  it("applies labeled filters, keyset pagination, and presents detail as bounded key/value rows", async () => {
    const user = userEvent.setup();
    render(<HomeContext.Provider value={adminHome}><EventsPage /></HomeContext.Provider>);
    expect(await screen.findByText("device_status")).toBeVisible();
    expect(screen.getByText(/21:00:00/, { selector: "time" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Tipo de evento"), { target: { value: "camera_capture" } });
    await user.selectOptions(screen.getByLabelText("Dispositivo"), "device-a");
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2026-09-01T00:00" } });
    fireEvent.change(screen.getByLabelText("Hasta"), { target: { value: "2026-09-01T23:59" } });
    await user.click(screen.getByRole("button", { name: "Aplicar filtros" }));
    expect(calls.some((url) => url.includes("event_type=camera_capture") && url.includes("device_id=device-a") && url.includes("date_from=2026-08-31T15%3A00%3A00.000Z") && url.includes("date_to=2026-09-01T14%3A59%3A00.000Z") && url.includes("limit=20"))).toBe(true);
    await user.click(screen.getByRole("button", { name: "Ver detalle de device_status" }));
    const dialog = await screen.findByRole("dialog", { name: "Detalle del evento" });
    expect(within(dialog).getByText("camera")).toBeVisible();
    expect(within(dialog).getByText("ready")).toBeVisible();
    expect(within(dialog).getByText(/21:00:00/, { selector: "time" })).toBeVisible();
    expect(within(dialog).queryByText(/\{"camera"/)).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cerrar" }));
    await user.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(calls.some((url) => url.includes("cursor="))).toBe(true);
    expect(screen.getByRole("button", { name: "Anterior" })).toBeEnabled();
  });

  it("shows audit only with the explicit administrative permission", async () => {
    const user = userEvent.setup();
    const view = render(<HomeContext.Provider value={adminHome}><EventsPage /></HomeContext.Provider>);
    await screen.findByText("device_status");
    await user.click(screen.getByRole("tab", { name: "Auditoría" }));
    expect(await screen.findByText("device.disabled")).toBeVisible();
    expect(screen.getByText(/21:00:00/, { selector: "time" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2026-09-01T00:00" } });
    fireEvent.change(screen.getByLabelText("Hasta"), { target: { value: "2026-09-01T23:59" } });
    await user.click(screen.getByRole("button", { name: "Aplicar filtros de auditoría" }));
    expect(calls.some((url) => url.includes("/audit?") && url.includes("date_from=2026-08-31T15%3A00%3A00.000Z") && url.includes("date_to=2026-09-01T14%3A59%3A00.000Z"))).toBe(true);
    expect(calls.some((url) => url.includes("/audit?"))).toBe(true);
    view.unmount(); calls = [];
    render(<HomeContext.Provider value={{ ...adminHome, role: "operator", permissions: ["events.read"] }}><EventsPage /></HomeContext.Provider>);
    await screen.findByText("device_status");
    expect(screen.queryByRole("tab", { name: "Auditoría" })).not.toBeInTheDocument();
    expect(calls.every((url) => !url.includes("/audit"))).toBe(true);
  });

  it("keeps an invalid daylight-saving boundary in the filter for correction", async () => {
    const user = userEvent.setup();
    const newYorkHome = { ...adminHome, timezone: "America/New_York" };
    render(<HomeContext.Provider value={newYorkHome}><EventsPage /></HomeContext.Provider>);
    await screen.findByText("device_status");
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2026-03-08T02:30" } });
    await user.click(screen.getByRole("button", { name: "Aplicar filtros" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no existe en la zona horaria/i);
    expect(screen.getByLabelText("Desde")).toHaveValue("2026-03-08T02:30");
  });

  it("keeps an invalid audit boundary in its modal-free filter for correction", async () => {
    const user = userEvent.setup();
    const newYorkHome = { ...adminHome, timezone: "America/New_York" };
    render(<HomeContext.Provider value={newYorkHome}><EventsPage /></HomeContext.Provider>);
    await screen.findByText("device_status");
    await user.click(screen.getByRole("tab", { name: "Auditoría" }));
    await screen.findByText("device.disabled");
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2026-03-08T02:30" } });
    await user.click(screen.getByRole("button", { name: "Aplicar filtros de auditoría" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no existe en la zona horaria/i);
    expect(screen.getByLabelText("Desde")).toHaveValue("2026-03-08T02:30");
  });
});

it("renders chart tooltip/legend affordances and a visible accessible data table", async () => {
  render(<HomeContext.Provider value={adminHome}><StatisticsPage /></HomeContext.Provider>);
  expect(await screen.findByRole("heading", { name: "Estadísticas" })).toBeVisible();
  expect(screen.getByRole("region", { name: "Gráfico de actividad" })).toBeVisible();
  expect(within(screen.getByRole("list", { name: "Leyenda del gráfico" })).getByText("Eventos")).toBeVisible();
  const table = screen.getByRole("table", { name: "Actividad diaria" });
  expect(within(table).getByText("7", { selector: "td" })).toBeVisible();
  expect(screen.getByText(/24 eventos, 9 conversaciones y 6 capturas/i)).toBeVisible();
});
