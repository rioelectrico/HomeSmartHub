import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AgentForm } from "@/components/agent/agent-form";
import { agentSchema } from "@/lib/schemas/agent";

const savedAgent = { name: "Portero", system_prompt: "Recibí a visitantes.", language: "es-AR", voice: "default", voice_speed: 1, realtime_model: "env-default", enabled: false, updated_at: null };

describe("AgentForm", () => {
  it("restores the last saved values and updates that baseline after saving", async () => {
    const user = userEvent.setup();
    render(<AgentForm initialValue={savedAgent} onSave={async (value) => ({ ...value, updated_at: "2026-09-05T12:00:00Z" })} />);
    const name = screen.getByLabelText(/nombre del agente/i);
    await user.clear(name);
    await user.type(name, "Cambio");
    await user.click(screen.getByRole("button", { name: /restaurar/i }));
    expect(name).toHaveValue("Portero");
    await user.clear(name);
    await user.type(name, "Recepción");
    await user.click(screen.getByRole("button", { name: /guardar/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/guardad/i);
    await user.clear(name);
    await user.click(screen.getByRole("button", { name: /restaurar/i }));
    expect(name).toHaveValue("Recepción");
  });

  it("announces inline validation and preserves edits on a rejected save", async () => {
    const user = userEvent.setup();
    render(<AgentForm initialValue={savedAgent} onSave={async () => { throw new Error("offline"); }} />);
    const name = screen.getByLabelText(/nombre del agente/i);
    await user.clear(name);
    await user.click(screen.getByRole("button", { name: /guardar/i }));
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent(/nombre/i);
    await user.type(name, "Recepción");
    await user.click(screen.getByRole("button", { name: /guardar/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no pudimos guardar/i);
    expect(name).toHaveValue("Recepción");
  });

  it("does not expose save or restore without edit permission", () => {
    render(<AgentForm initialValue={savedAgent} onSave={async (value) => ({ ...value, updated_at: null })} canEdit={false} />);
    expect(screen.getByLabelText(/nombre del agente/i)).toBeDisabled();
    expect(screen.queryByRole("button", { name: /guardar|restaurar/i })).not.toBeInTheDocument();
  });

  it.each([
    ["name", ""], ["name", "x".repeat(129)], ["system_prompt", ""], ["system_prompt", "x".repeat(8001)],
    ["language", "e"], ["language", "x".repeat(33)], ["voice", ""], ["voice", "x".repeat(129)],
    ["voice_speed", 0.49], ["voice_speed", 2.01], ["realtime_model", ""], ["realtime_model", "x".repeat(129)],
  ])("rejects backend-invalid %s values", (field, value) => {
    expect(agentSchema.safeParse({ ...savedAgent, [field]: value }).success).toBe(false);
  });

  it("accepts the backend boundary values", () => {
    expect(agentSchema.safeParse({ ...savedAgent, name: "x".repeat(128), system_prompt: "x".repeat(8000), language: "es", voice_speed: 0.5 }).success).toBe(true);
    expect(agentSchema.safeParse({ ...savedAgent, voice_speed: 2 }).success).toBe(true);
  });
});
