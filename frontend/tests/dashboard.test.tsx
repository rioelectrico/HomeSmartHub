import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "@/app/(panel)/dashboard/page";
import { HomeContext } from "@/components/home-context";

const home = { id: "home-a", name: "Casa", timezone: "America/Argentina/Buenos_Aires", role: "owner", permissions: ["events.read", "devices.read"] };
const statistics = { timezone: home.timezone, generated_at: "2026-09-05T12:00:00Z", today: { events: 8, conversations: 3, captures: 2 }, last_7_days: { events: 24, conversations: 9, captures: 6 }, daily: Array.from({ length: 7 }, (_, i) => ({ date: `2026-09-0${i + 1}`, events: i, conversations: 0, captures: 0 })), devices: { total: 1, online: 0, offline: 1, disabled: 0, provisioning: 0 } };
const diagnostics = { services: { backend: "up", postgresql: "up", openai: "unconfigured" }, devices: { items: [{ id: "device-a", device_id: "PI-000001", name: "Entrada", status: "offline", connected: false, last_seen_at: null, snapshot_at: null, snapshot: { firmware_version: "1.2.3", camera: "ready" } }], next_cursor: null } };
function renderDashboard(permissions = home.permissions) { return render(<HomeContext.Provider value={{ ...home, permissions }}><DashboardPage /></HomeContext.Provider>); }

describe("Dashboard", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/statistics") ? Response.json(statistics) : Response.json(diagnostics)));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders real counts, device diagnostics, timestamps and an honest capture state", async () => {
    renderDashboard();
    expect(screen.getAllByRole("status").some((node) => /cargando/i.test(node.textContent ?? ""))).toBe(true);
    expect(await screen.findByRole("heading", { name: "Entrada" })).toBeVisible();
    expect(within(screen.getByRole("article", { name: "Eventos hoy" })).getByText("8")).toBeVisible();
    expect(screen.getByText("1.2.3")).toBeVisible();
    expect(screen.getByText(/sin conexión/i)).toBeVisible();
    expect(screen.getByText(/OpenAI: sin configurar/i)).toBeVisible();
    expect(screen.getByText(/captura no disponible/i)).toBeVisible();
    expect(screen.getAllByText(/actualizado/i).length).toBeGreaterThan(0);
  });

  it("shows empty device state without inventing online status", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/statistics") ? Response.json(statistics) : Response.json({ ...diagnostics, devices: { items: [], next_cursor: null } })));
    renderDashboard();
    expect(await screen.findByText(/no hay dispositivos/i)).toBeVisible();
    expect(screen.queryByText("Todo en orden")).not.toBeInTheDocument();
  });

  it("allows retry after errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(null, { status: 500 })).mockResolvedValue(Response.json(statistics)));
    renderDashboard(["events.read"]);
    expect(await screen.findByRole("alert")).toHaveTextContent(/no pudimos cargar/i);
    await userEvent.setup().click(screen.getByRole("button", { name: /reintentar/i }));
    expect(await screen.findByRole("article", { name: "Eventos hoy" })).toHaveTextContent("8");
  });

  it("does not request resources without the selected home's permission", async () => {
    renderDashboard([]);
    expect(screen.getByText(/sin permiso para ver estadísticas/i)).toBeVisible();
    expect(screen.getByText(/sin permiso para ver diagnósticos/i)).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });
});
